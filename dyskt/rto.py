#!/usr/bin/env python

""" rto.py: collates and relays wraith sensor data

Processes and forwards 1) frames from the sniffer(s) 2) gps data (if any) collected
by a pf and 3) messages from tuner(s) and sniffer(s0
"""
__name__ = 'rto'
__license__ = 'GPL v3.0'
__version__ = '0.0.12'
__date__ = 'March 2015'
__author__ = 'Dale Patterson'
__maintainer__ = 'Dale Patterson'
__email__ = 'wraith.wireless@yandex.com'
__status__ = 'Development'

import platform                            # system details
import sys                                 # python intepreter details
import signal                              # signal processing
import time                                # sleep and timestamps
import socket                              # connection to nidus and gps device
import ssl                                 # encrypted comms w/ nidus
import threading                           # threads
import mgrs                                # lat/lon to mgrs conversion
import gps                                 # gps device access
import zlib                                # compress bulk frames
from Queue import Queue, Empty             # thread-safe queue
import multiprocessing as mp               # multiprocessing
import wraith                              # for cert path
from wraith.utils.timestamps import ts2iso # timestamp conversion
from wraith.radio.iw import regget         # regulatory domain

# CONSTANTS
_BSZ_ = 14336   # size in bytes
_BTM_ = 1000    # time in milliseconds

class GPSPoller(threading.Thread):
    """ periodically checks gps for current location """
    def __init__(self,eq,conf):
        """
         eq - event queue between rto and this Thread
         conf - config file for gps
        """
        threading.Thread.__init__(self)
        self._done = threading.Event() # stop event
        self._eQ = eq                  # msg queue from radio controller
        self._conf = conf              # gps configuration
        self._gpsd = None
        self._setup()

    def _setup(self):
        """ attempt to connect to device if fixed is off """
        if self._conf['fixed']: return # static, do nothing
        try:
            self._gpsd = gps.gps('127.0.0.1',self._conf['port'])
            self._gpsd.stream(gps.WATCH_ENABLE)
        except socket.error as e:
            raise RuntimeError(e)

    def shutdown(self): self._done.set()

    def run(self):
        """ query for current location """
        # two execution loops - 1) get device details 2) get location

        # initialize device details dict
        if self._conf['fixed']:
            # configure a 'fake' device
            poll = 0.5 # please poll charm
            qpx = qpy = float('inf') # quiet PyCharm alerts
            dd = {'id':'xxxx:xxxx',
                  'version':'0.0',
                  'flags':0,
                  'driver':'static',
                  'bps':0,
                  'path':'None'}
        else:
            poll = self._conf['poll'] # extract polltime from config
            qpx = self._conf['epx']   # extract quality ellipse x from config
            qpy = self._conf['epy']   # extract quality ellipse x from config
            dd = {'id':self._conf['id'],
                  'version':None,
                  'driver':None,
                  'bps':None,
                  'path':None,
                  'flags':None}

        # Device Details Loop - get complete details or quit on done
        while dd['flags'] is None:
            if self._done.is_set(): break
            else:
                # loop while data is on serial, polling first
                while self._gpsd.waiting():
                    dev = self._gpsd.next()
                    try:
                        if dev['class'] == 'VERSION': dd['version'] = dev['release']
                        elif dev['class'] == 'DEVICE':
                            # flags will not be present until gpsd has seen identifiable
                            # packets from the device
                            dd['flags'] = dev['flags']
                            dd['driver'] = dev['driver']
                            dd['bps'] = dev['bps']
                            dd['path'] = dev['path']
                    except KeyError:
                        pass

        # send device details. If fixed gps, exit immediately
        if not self._done.is_set():
            self._eQ.put(('!DEV!',time.time(),dd))
            if self._conf['fixed']: # send static front line trace
                self._eQ.put(('!FLT!',time.time(),{'id':dd['id'],
                                                  'fix':-1,
                                                  'lat':(self._conf['lat'],0.0),
                                                  'lon':(self._conf['lon'],0.0),
                                                  'alt':self._conf['alt'],
                                                  'dir':self._conf['dir'],
                                                  'spd':0.0,
                                                  'dop':{'xdop':1,'ydop':1,'pdop':1}}))
                return

        # Location - loop until told to quit
        while not self._done.is_set():
            # while there's data, get it (NOTE: this loop may exit without data)
            while self._gpsd.waiting():
                if self._done.is_set(): break
                rpt = self._gpsd.next()
                if rpt['class'] != 'TPV': continue
                try:
                    if rpt['epx'] > qpx or rpt['epy'] > qpy: continue
                    else:
                        # get all values
                        flt = {}
                        flt['id'] = dd['id']
                        flt['fix'] = rpt['mode']
                        flt['lat'] = (rpt['lat'],rpt['epy'])
                        flt['lon'] = (rpt['lon'],rpt['epx'])
                        flt['alt'] = rpt['alt'] if 'alt' in rpt else float("nan")
                        flt['dir'] = rpt['track'] if 'track' in rpt else float("nan")
                        flt['spd'] = rpt['spd'] if 'spd' in rpt else float("nan")
                        flt['dop'] = {'xdop':self._gpsd.xdop,
                                       'ydop':self._gpsd.ydop,
                                       'pdop':self._gpsd.pdop}
                        self._eQ.put(('!GEO',time.time(),flt))
                        break
                except (KeyError,AttributeError):
                    # a KeyError means not all values are present, an
                    # AttributeError means not all dop values are present
                    pass
            time.sleep(poll)

        # close out connection
        self._gpsd.close()

class RTO(mp.Process):
    """ RTO - handles further processing of raw frames from the sniffer """
    def __init__(self,comms,conn,conf):
        """
         initializes RTO
         comms - internal communication
          NOTE: all messages sent on internal comms must be a tuple T where
           T = (sender callsign,timestamp of event,type of message,event message)
         conn - connection to/from DySKT
         conf - necessary config details
        """
        mp.Process.__init__(self)
        self._icomms = comms               # communications queue
        self._conn = conn                  # message queue to/from DySKT
        self._mgrs = None                  # lat/lon to mgrs conversion
        self._conf = conf['gps']           # configuration for gps/datastore
        self._nidus = None                 # nidus server
        self._gps = None                   # polling thread for front line traces
        self._q = None                     # internal queue for gps poller
        self._bulk = {}                    # stored frames
        self._setup(conf['store']['host'], # nidus storage manager addres/port
                    conf['store']['port'])

    def _setup(self,host,port):
        """ connect to Nidus for data transfer and pass sensor up event """
        try:
            # get mgrs converter
            self._mgrs = mgrs.MGRS()

            # connect to data store
            self._nidus = ssl.wrap_socket(socket.socket(socket.AF_INET,
                                                        socket.SOCK_STREAM),
                                          ca_certs=wraith.NIDUSCERT,
                                          cert_reqs=ssl.CERT_REQUIRED,
                                          ssl_version=ssl.PROTOCOL_TLSv1)
            self._nidus.connect((host,port))
        except socket.error as e:
            raise RuntimeError("RTO:Nidus:%s" % e)
        except Exception as e:
            raise RuntimeError("RTO:Unknown:%s" % e)

    def terminate(self): pass
    
    def run(self):
        """ run execution loop """
        # ignore signals being used by main program
        signal.signal(signal.SIGINT,signal.SIG_IGN)
        signal.signal(signal.SIGTERM,signal.SIG_IGN)

        # basic variables
        rmap = {}    # radio map: maps callsigns to mac addr
        gpsid = None # id of gps device

        # send sensor up notification, platform details and gpsid
        ret = self._send('DEVICE',time.time(),['sensor',socket.gethostname(),1])
        if ret: self._conn.send(('err','RTO','Nidus',ret))
        else:
            ret = self._send('PLATFORM',time.time(),self._pfdetails())
            if ret: self._conn.send(('err','RTO','Nidus',ret))
            else: self._setgpsd()

        # execution loop
        while True:
            # 1. anything from DySKT
            if self._conn.poll() and self._conn.recv() == '!STOP!': break

            # 2. gps device/frontline trace?
            try:
                t,ts,msg = self._q.get_nowait()
            except (Empty,AttributeError): pass
            else:
                if t == '!DEV!': # device up message
                    gpsid = msg['id']
                    self._conn.send(('info','RTO','GPSD',"%s initiated" % gpsid))
                    ret = self._send('GPSD',ts,msg)
                    if ret: self._conn.send(('err','RTO','Nidus',ret))
                elif t == '!FLT!': # frontline trace
                    ret = self._send('FLT',ts,msg)
                    if ret: self._conn.send(('err','RTO','Nidus',ret))

            # 3. queued data from internal comms
            ev = msg = None
            try:
                rpt = self._icomms.get(True,0.5)
                cs,ts,ev,msg = rpt[0],rpt[1],rpt[2],rpt[3]

                if ev == '!UP!': # should be the 1st message we get from radio(s)
                    # NOTE: send the radio, nidus will take care of setting the
                    # radio device status, initial radio events and using_radio.
                    # Send each antenna separately
                    rmap[cs] = msg['mac']
                    self._bulk[cs] = {'cob':None,       # zlib compressobj
                                      'mac':msg['mac'], # mac addr of collecting radio
                                      'start':0,        # time first frame was seen
                                      'cnt':0,          # ttl # of frames
                                      'sz':0,           # ttl size of uncompressed frames
                                      'frames':''}      # the compressed frames
                    self._conn.send(('info','RTO','Radio',"%s initiated" % cs))
                    ret = self._send('RADIO',ts,msg)
                    if ret: self._conn.send(('err','RTO','Nidus',ret))
                    else:
                        # send antennas
                        for i in xrange(msg['nA']):
                            ret = self._send('ANTENNA',ts,
                                             {'mac':msg['mac'],'index':i,
                                              'type':msg['type'][i],'gain':msg['gain'][i],
                                              'loss':msg['loss'][i],'x':msg['x'][i],
                                              'y':msg['y'][i],'z':msg['z'][i]})
                            if ret: self._conn.send(('err','RTO','Nidus',ret))
                elif ev == '!FAIL!':
                    # send bulked frames, notify nidus & DySKT & delete cs
                    ret = self._flushbulk(ts,rmap[cs],cs)
                    if not ret: ret = self._send('RADIO_EVENT',ts,[rmap[cs],'fail',msg])
                    if ret: self._conn.send(('err','RTO','Nidus',ret))
                    del rmap[cs]
                    del self._bulk[cs]
                elif ev == '!SCAN!':
                    # compile the scan list into a string before sending
                    sl = ",".join(["%s:%s" % (c,w) for (c,w) in msg])
                    self._conn.send(('info',cs,ev.replace('!',''),sl))
                    ret = self._send('RADIO_EVENT',ts,[rmap[cs],'scan',sl])
                    if ret: self._conn.send(('err','RTO','Nidus',ret))
                elif ev == '!LISTEN!':
                    self._conn.send(('info',cs,ev.replace('!',''),msg))
                    ret = self._send('RADIO_EVENT',ts,[rmap[cs],'listen',msg])
                    if ret: self._conn.send(('err','RTO','Nidus',ret))
                elif ev == '!HOLD!':
                    self._conn.send(('info',cs,ev.replace('!',''),msg))
                    ret = self._send('RADIO_EVENT',ts,[rmap[cs],'hold',msg])
                    if ret: self._conn.send(('err','RTO','Nidus',ret))
                elif ev == '!PAUSE!':
                    # send bulked frames
                    self._conn.send(('info',cs,ev.replace('!',''),msg))
                    ret = self._flushbulk(ts,rmap[cs],cs)
                    if not ret: ret = self._send('RADIO_EVENT',ts,[rmap[cs],'pause',' '])
                    if ret: self._conn.send(('err','RTO','Nidus',ret))
                elif ev == '!SPOOF!':
                    self._conn.send(('info',cs,ev.replace('!',''),msg))
                    ret = self._send('RADIO_EVENT',ts,[rmap[cs],'spoof',msg])
                    if ret: self._conn.send(('err','RTO','Nidus',ret))
                elif ev == '!TXPWR!':
                    self._conn.send(('info',cs,ev.replace('!',''),msg))
                    ret = self._send('RADIO_EVENT',ts,[rmap[cs],'txpwr',msg])
                    if ret: self._conn.send(('err','RTO','Nidus',ret))
                elif ev == '!DWELL!':
                    if msg: self._conn.send(('info',cs,ev.replace('!',''),msg))
                elif ev == '!FRAME!':
                    # save the frame and update bulk details
                    if self._bulk[cs]['cnt'] == 0:
                        self._bulk[cs]['start'] = ts               # reset start
                        self._bulk[cs]['cob'] = zlib.compressobj() # new compression obj

                    # add new frame and update metrics
                    self._bulk[cs]['cnt'] += 1
                    self._bulk[cs]['sz'] += len(msg)
                    self._bulk[cs]['frames'] += self._bulk[cs]['cob'].compress('%s \x1EFB\x1F%s\x1FFE\x1E' % (ts2iso(ts),msg))

                    # if we have hit our limit, compress and send the bulk frames
                    if self._bulk[cs]['sz'] > _BSZ_ or (ts - self._bulk[cs]['start'])/1000 > _BTM_:
                        ret = self._flushbulk(ts,rmap[cs],cs)
                        if ret: self._conn.send(('err','RTO','Nidus',ret))
                else: # unidentified event type, notify dyskt
                    self._conn.send(('warn','RTO','Radio',"unknown event %s" % ev))
            except Empty: continue
            except IndexError: # something wrong with antenna indexing
                self._conn.send(('err','RTO',"Radio","misconfigured antennas"))
            except KeyError as e: # a radio sent a message without initiating
                self._conn.send(('err','RTO','Radio %s' % e,"data out of order (%s)" % ev))
            except Exception as e: # handle catchall error
                self._conn.send(('err','RTO','Unknown',e))

        # any bulked frames not yet sent?
        for cs in rmap:
            ret = self._flushbulk(time.time(),rmap[cs],cs)
            if ret:
                if ret: self._conn.send(('err','RTO','Nidus',ret))
                break

        # notify Nidus of closing (radios,sensor,gpsd). hopefully no errors on send
        ts = time.time()
        for cs in rmap: self._send('DEVICE',ts,['radio',rmap[cs],0])
        self._send('DEVICE',ts,['gpsd',gpsid,0])
        self._send('DEVICE',ts,['sensor',socket.gethostname(),0])

        # shut down
        if not self._shutdown():
            try:
                self._conn.send(('warn','RTO','Shutdown',"Incomplete shutdown"))
            except IOError:
                # most likely DySKT(.py) closed their side of the pipe
                pass

        #### private helper functions

    def _shutdown(self):
        """ clean up. returns whether a full reset or not occurred """
        # try shutting down & resetting radio (if it failed we may not be able to)
        clean = True
        try:
            # close socket to storage manager and connection
            self._nidus.close()
            self._conn.close()

            # stop GPSPoller
            if self._gps and self._gps.is_alive(): self._gps.stop()
        except:
            clean = False
        return clean

    def _flushbulk(self,ts,mac,rdo):
        """ send bulked frames belonging to rdo with mac addr """
        # if no stored frames, exit
        if self._bulk[rdo]['sz'] == 0: return None
        try:
            self._bulk[rdo]['frames'] += self._bulk[rdo]['cob'].flush()
            ret = self._send('BULK',ts,[mac,self._bulk[rdo]['cnt'],
                                        self._bulk[rdo]['frames']])
            return ret
        except zlib.error as e: self._conn.send(('err','RTO','zlib',e))
        finally:
            # reset bulk details
            self._bulk[rdo]['cnt'] = 0
            self._bulk[rdo]['sz'] = 0
            self._bulk[rdo]['frames'] = ''

    def _setgpsd(self):
        """ determines whether to use no gps, fixed gps or gps device """
        #if not self._conf: return # if there is no gps config, do nothing
        self._q = Queue()
        try:
            self._gps = GPSPoller(self._q,self._conf)
            self._gps.start()
        except RuntimeError as e:
            self._conn.send(('warn','RTO','FLT',"Failed to connnect %s" %e))
            self._gps = None

    @staticmethod
    def _pfdetails():
        """ get platform details as dict and return """
        d = {'rd':None,'dist':None,'osvers':None,'name':None}
        d['os'] = platform.system().capitalize()
        try:
            d['dist'],d['osvers'],d['name'] = platform.linux_distribution()
        except:
            pass
        try:
            d['rd'] = regget()
        except:
            pass
        d['kernel'] = platform.release()
        d['arch'] = platform.machine()
        d['pyvers'] = "%d.%d.%d" % (sys.version_info.major,
                                    sys.version_info.minor,
                                    sys.version_info.micro)
        d['bits'],d['link'] = platform.architecture()
        d['compiler'] = platform.python_compiler()
        d['libcvers'] = " ".join(platform.libc_ver())
        return d

    #### send helper functions

    def _send(self,t,ts,d):
        """
         send - sends message msg m of type t with timestamp ts to Nidus
          t - message type
          ts - message timestamp
          d - data
         returns None on success otherwise returns reason for failure
        """
        # convert the timestamp to utc isoformat before crafting
        ts = ts2iso(ts)
        
        # craft the message
        try:
            send = "\x01*%s:\x02" % t
            if t == 'DEVICE': send += self._craftdevice(ts,d)
            elif t == 'PLATFORM': send += self._craftplatform(d)
            elif t == 'RADIO': send += self._craftradio(ts,d)
            elif t == 'ANTENNA': send += self._craftantenna(ts,d)
            elif t == 'GPSD': send += self._craftgpsd(ts,d)
            elif t == 'FRAME': send += self._craftframe(ts,d)
            elif t == 'BULK': send += self._craftbulk(ts,d)
            elif t == 'FLT': send += self._craftflt(ts,d,self._mgrs)
            elif t == 'RADIO_EVENT': send += self._craftradioevent(ts,d)
            send += "\x03\x12\x15\04"
            if not self._nidus.send(send): return "Nidus socket closed unexpectantly"
            return None
        except socket.error, ret:
            return ret
        except Exception, ret:
            return ret

    @staticmethod
    def _craftdevice(ts,d):
        """ create body of device message """
        return "%s %s \x1EFB\x1F%s\x1FFE\x1E %d" % (ts,d[0],d[1],d[2])

    @staticmethod
    def _craftplatform(d):
        """ create body of platform message """
        return "%s \x1EFB\x1F%s\x1FFE\x1E %s \x1EFB\x1F%s\x1FFE\x1E %s %s %s %s %s \x1EFB\x1F%s\x1FFE\x1E \x1EFB\x1F%s\x1FFE\x1E \x1EFB\x1F%s\x1FFE\x1E" % \
                (d['os'],d['dist'],d['osvers'],d['name'],d['kernel'],d['arch'],
                 d['pyvers'],d['bits'],d['link'],d['compiler'],d['libcvers'],d['rd'])

    @staticmethod
    def _craftradio(ts,d):
        """ creates radio message body """
        return "%s %s %s \x1EFB\x1F%s\x1FFE\x1E %s %s %s \x1EFB\x1F%s\x1FFE\x1E \x1EFB\x1F%s\x1FFE\x1E %s %s %s \x1EFB\x1F%s\x1FFE\x1E"  % \
               (ts,d['mac'],d['role'],d['spoofed'],d['phy'],d['nic'],d['vnic'],
                d['driver'],d['chipset'],d['standards'],','.join(d['channels']),
                d['txpwr'],d['desc'])

    @staticmethod
    def _craftradioevent(ts,d):
        """ create body of radio event message """
        return "%s %s %s \x1EFB\x1F%s\x1FFE\x1E" % (ts,d[0],d[1],d[2])

    @staticmethod
    def _craftantenna(ts,d):
        """ create body of antenna message """
        return "%s %s %d \x1EFB\x1F%s\x1FFE\x1E %.2f %.2f %d %d %d" %\
               (ts,d['mac'],d['index'],d['type'],d['gain'],d['loss'],d['x'],
                d['y'],d['z'])

    @staticmethod
    def _craftbulk(ts,d):
        """ create body of bulk message """
        return "%s %s %s \x1EFB\x1F%s\x1FFE\x1E" % (ts,d[0],d[1],d[2])

    @staticmethod
    def _craftframe(ts,d):
        """ creates body of the frame message """
        return "%s \x1EFB\x1F%s\x1FFE\x1E \x1EFB\x1F%s\x1FFE\x1E" % (ts,d[0],d[1])

    @staticmethod
    def _craftgpsd(ts,d):
        """ create body of gps device message """
        return "%s %s %s %d \x1EFB\x1F%s\x1FFE\x1E %d \x1EFB\x1F%s\x1FFE\x1E" %\
               (ts,d['id'],d['version'],d['flags'],d['driver'],d['bps'],d['path'])

    @staticmethod
    def _craftflt(ts,d,m):
        """ create body of the gps location message """
        return "%s %s %d %s %.2f %.2f %.2f %.3f %.3f %.3f %.3f %.3f" %\
               (ts,d['id'],d['fix'],m.toMGRS(d['lat'][0],d['lon'][0]),d['alt'],
                d['dir'],d['spd'],d['lat'][1],d['lon'][1],d['dop']['xdop'],
                d['dop']['ydop'],d['dop']['pdop'])
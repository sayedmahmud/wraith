# ![](widgets/icons/wraith2.png?raw=true) WRAITH: Wireless assault, reconnaissance, collection and exploitation toolkit.

> "You knew that I reap where I have not sown and gather where I scattered no seed?"

## 1 DESCRIPTION:
Attack vectors, rogue devices, interfering networks are best visualized and identified over time. 
Current tools i.e. Kismet, Aircrack-ng and Wireshark are excellent tools but none are completely 
suitable for collecting and analyzing the 802.11 environment over a period of time (without implementing a 
custom interface). 

Wraith is an attempt to develop a toolsuite that eases the collection, collation and analysis of temporal 
802.11 data in order to provide administrators with the ability to view their networks from a bird's eye 
view and drill down as necessary to a single device.

Once the reconnaissance and collection development is stable, assault plug-ins will be developed to aid
WLAN administrators in the security testing of their networks. 

## 2. REQUIREMENTS: 
 * linux (preferred 3.x kernel, tested on 3.13.0-43)
   - NOTE: some cards i.e. rosewill usb nics were not fully supported through iw
     on earlier kernels
 * Python 2.7
 * iw 3.17
 * postgresql 9.x (tested on 9.3.5)
 * pyscopg 2.5.3
 * mgrs 1.1

## 3. MODULES: Currently consists of four components/modules

###  a. Radio: 802.11 network interface objects and functions

Objects/functions to manipulate wireless nics and parse 802.11 captures.
Partial support of 802.11-2012

#### Standards
* Currently Supported: 802.11a\b\g
* Partially Supported: 802.11n
* Not Supported: 802.11s\y\u\ac\ad\af

### b. Suckt: Small Unit Capture/Kill Team (Wraith Sensor)

Suckt is a 802.11 sensor consisting of an optional collection radio (i.e.
spotter), a mandatory reconnaissance radio (i.e. shooter) and an RTO which relays
collected data to Nidus, the data storage system (i.e. HQ). Suckt collects data
in the form of raw 802.11 packets with the reconnaissance (and collection if present)
radios, forwarding that date along with any geolocational data (if a gps device
is present) to higher. The reconnaissance radio will also partake in assaults in
directed to.

### c. Nidus: Data Storage Manager

Nidus is the Data Storage manager processing data received from Suckt. Nidus is the 
interface to the backend Postgresql database, processing data in terms of raw 802.11
frames, gps location, and 'device' details/status. 

### d. GUI: non-operational gui

## 4. ARCHITECTURE/HEIRARCHY: Brief Overview of the project file structure

* wraith/                Top-level package
 - \_\_init\_\_.py          this file - initialize the top-level (includes misc functions)
 - wraith-rt.py         the gui
 -    LICENSE              software license
 -    README.txt           details
 -    CONFIGURE.txt        setup details
 *    widgets              gui subpackage
      *  icons            icons folder
      -  \_\_init\_\_.py      initialize widgets subpackage
      -  panel.py         defines Panel and subclasses for gui
*  radio                subpackage for radio/radiotap
 - \_\_init\_\_.py      initialize radio subpackage
 - bits.py          bitmask related funcs, bit extraction functions
 - iwtools.py       iwconfig, ifconfig interface and nic utilities
 - iw.py            iw 3.17 interface
 - radiotap.py      radiotap parsing
 - mpdu.py          IEEE 802.11 MAC (MPDU) parsing
 - infoelement.py   contstants for mgmt frames
 - channels.py      802.11 channel, freq utilities
 - mcs.py           mcs index functions
 - oui.py           oui/manuf related functions
*  suckt                subpackage for wraith sensor
 - \_\_init\_\_.py      initialize suckt package
 - suckt.conf       configuration file for wasp
 - suckt.log.conf   configuration file for wasp logging
 - suckt.py         primary module
 - internal.py      defines the Report class
 - rdoctl.py        radio controler with tuner, sniffer
 - rto.py           data collation and forwarding
 - sucktd           sucktd daemon
*  nidus                subpackage for datamanager
 - \_\_init\_\_.py      initialize nidus package
 - nidus.conf       nidus configuration
 - nidus.log.conf   nidus logging configuration
 - nidus.py         nidus server
 - nmp.py           nidus protocol definition
 - nidusdb.py       interface to storage system
 - simplepcap.py    pcap writer
 - nidus.sql        sql tables definition
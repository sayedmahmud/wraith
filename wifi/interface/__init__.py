#!/usr/bin/env python

""" interface: 802.11 wireless network interface functionality

Objects/functions to manipulate and query wireless nics

REVISIONS:
interface 0.0.2
 desc: provides tools to manipulate wireless nics 
 includes: radio 0.0.2 iw 0.1.0 iwtools 0.0.12, oui 0.0.3, sockios_h 0.0.1,
  nl80211_h 0.0.1
 changes:
  - added Radio class to consolidate iw.py and iwtools.py
   o implements gethwaddr,getifindex,getflags (using ioctl)
  - added sockios_h for sock ioctl constants
  - added nl80211_h for nl80211 constants
  - added if_h for inet definitions
"""
__name__ = 'interface'
__license__ = 'GPL v3.0'
__version__ = '0.0.2'
__date__ = 'February 2016'
__author__ = 'Dale Patterson'
__maintainer__ = 'Dale Patterson'
__email__ = 'wraith.wireless@yandex.com'
__status__ = 'Development'

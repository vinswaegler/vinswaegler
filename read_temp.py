#!/usr/bin/python3
# use Python3!!

from onewire import *

for n in get_w1_names():
  print('sensor: ', n, ' temp: ', get_w1_temp(n))
  

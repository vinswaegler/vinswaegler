#!/usr/bin/python3
# use Python3!!
import argparse
import RPi.GPIO as GPIO
import time
import datetime
import sys
from onewire import *
from enum import Enum

dev = {'house': '28-02149245b4f2', 'collector': '28-021492459ef5', 'outside' : '28-02149245af81'}#, 'inlet' : '28-021792454ed3'

fan_gpio = 6
relay_gpio = 13


parser = argparse.ArgumentParser(description='Control a fan and relay for a solar air heater and night cooling')
parser.add_argument("--mode", help="control of temeratures", choices=['summer', 'heating'],required=True)
parser.add_argument('--span', type=float, default=1, help='hysteresis for full speed in °C')
parser.add_argument('--sleep', type=float, default=30, help='sleep time in seconds')
parser.add_argument('--overheat', type=float, default=50, help='max tolerable collector temperature')
parser.add_argument('--off', help='switch off and do only overheat control', action='store_true')


nice = "{:5.1f}"
frac = "{:3.2f}"
args = parser.parse_args()

#check if we have the sensors
sens = get_w1_names()
if len(sens) == 0:
  print('could not find w1 sensors')
  sys.exit()
print('# w1 sensors found: ', sens)
if dev['house'] not in sens:
  print('given house sensor ' + dev['house'] + ' not found in system')
  sys.exit()    
if dev['collector'] not in sens:
  print('given collector sensor ' + dev['collector'] + ' not found in system')
  sys.exit()    

# switch off fan by default
GPIO.setwarnings(False) # disable RuntimeWarning: This channel is already in use, continuing anyway.
GPIO.setmode(GPIO.BCM)


# our fan class holds a GPIO fan and sets duty cycle with a small smoother
class Fan:
      
  def __init__(self, gpio_pin, frequency = 20, historysize = 3):
    GPIO.setup(fan_gpio, GPIO.OUT)
    self.fan = GPIO.PWM(gpio_pin, frequency)
    self.fan.start(0) # switch off
    self.hist = [] # here we store the last 3 duty cycles
    self.historysize = historysize
    
     
  # set the averarged value from 0 ... 100. With > 100 set 100, don't average
  # @return actually set value 0 ... 100   
  def set_duty_cylce(self, value):
    # always remember value
    self.hist.append(min(value, 100))
    self.hist = self.hist[-self.historysize:]   

    new = 100 if value > 100 else sum(self.hist)/len(self.hist)  
    
    self.fan.ChangeDutyCycle(new)
    
    return new 

class State(Enum):
  OPEN    = 0 # no specific state
  TESTING = 1 # currently testing
  RESTING = 2 # waiting failed test to pass


# It can be that there is warm air left in the collector, then we want to blow - or
# it is cold but sunny and the collector is producing heat, then we cannot cool.
# SmartCooling is a simple state machine which allows controlled tests after a rest time 
# In the evening a test will cool down the collector, hence we need no further testing.
class SmartCooling:

  # dt args.sleep, how often we tic
  # test how many seconds we test if we can cool
  # rest if a test failed, how long to wait for the next test
  def __init__(self, dt, test = 660, rest = 2920):
    self.T = int(test/dt)
    self.R = int(rest/dt)
    
    # we check this only for to warm, outside < house and collector > house
    self.state = State.OPEN 

    self.time   = -1 # time is an int counter incremented by set
    self.switch = -1 # timestep when we switched state 
     
  # set new timestep, automatically resets state
  def step(self):   
    self.time += 1
    
    # are we in testing and end testing now? We do not need to check testing.
    # either inlet is cooler or it is not. 
    if self.state == State.TESTING and self.switch == self.time - self.T:
      self.state = State.RESTING
      self.switch = self.time
      
    # end resting?  
    if self.state == State.RESTING and self.switch == self.time - self.R:
      self.state = State.OPEN        
      self.switch = self.time
      
  # set testing mode in current time step
  def test(self):    
    assert self.state == State.OPEN
    self.state = State.TESTING 
    self.switch = self.time
    
# small helper class for relay to prevent too often switching
class RelayControl:
  
  #@param dt tic time in sec
  #@param wait minimal switch time in seconds
  def __init__(self, relay_gpio, dt, wait):    
    self.W = int(wait/dt)
    self.gpio = relay_gpio
    # switch off relay be default
    GPIO.setup(relay_gpio, GPIO.OUT)
    GPIO.output(relay_gpio, GPIO.LOW)
    self.wait_counter = 0
    self.out = 0 # LOW
    
  #@state True or False, action ignored when in wait_counter
  #@return current state as 0 or 1 (desired or old when ignored)   
  def set(self, state):  
    if self.wait_counter <= 0:
      GPIO.output(relay_gpio, GPIO.HIGH if state else GPIO.LOW)
      self.out = 1 if state else 0
      # set wait before we can do the next action
      self.wait_counter = self.W
    else:
      self.wait_counter -= 1
    
    return self.out, (self.wait_counter, state) 
     
     
# determine target temperature
# this function opens the way to play with different cooling and heating targets -> much room to extend!
# @mode summer - colder night target to do night cooling
# @delta transitition time for night temperature switch in seconds
def target_temp(house, mode, now = None, delta = 7200):
  assert mode == 'summer' or 'heating'     
  summer = mode == 'summer'
  
  night_temp = 20 if summer else 22
  day_temp   = 22 if summer else 24 
  
  # https://stackoverflow.com/questions/1831410/how-to-compare-times-in-python
  cmp = datetime.datetime.now() if now == None else now
  # when we start night cooling
  night_start = cmp.replace(hour=1, minute=0, second=0, microsecond=0)
  # when we end night cooling
  night_end   = cmp.replace(hour=8, minute=0, second=0, microsecond=0)
  # sufficiently after night end to not heat too early in summer 
  day_start   = cmp.replace(hour=(10 if summer else 8), minute=0, second=0, microsecond=0)
  
  # do cool if we are above, but don't switch on hard 
  cooling_target = night_temp if cmp > night_start and cmp < night_end else day_temp
  
  # do cool if we are above, but don't switch on hard - make is step by step before having a too long line to debug
  cooling_target = None
  if cmp < night_start or cmp > night_end:
    cooling_target = day_temp
  else:
    # we are in the night, if late enough, make cold
    assert cmp > night_start and cmp < night_end
    if (cmp - night_start).seconds > delta: # timedelta has only days, seconds and microseconds
      cooling_target = night_temp
    else: 
      # linearly decrease within range the target temperature
      cooling_target = day_temp - (day_temp-night_temp) * (cmp - night_start).seconds / delta   
  
  # do heat if we are below
  heating_target = night_temp if cmp > night_start and cmp < day_start else day_temp
  # we need decide if we shall set target to heat or cool
  
  # no brainer case
  if cooling_target == heating_target:
    return cooling_target

  # good enough if no need to cool and no need to heat
  if house <= cooling_target and house >= heating_target:
    return house
  
  # we need cooling but not heating  
  if house >= cooling_target and house >= heating_target:
    return cooling_target 

  # we need no cooling but need heating
  if house <= cooling_target and house <= heating_target:  
    return heating_target
  
  # remaining case: we need cooling and we need heating - looks like a misconfiguration 
  if house >= cooling_target and house <= heating_target:
    print('misconfiguration?! house', house, 'cooling_target',cooling_target,'heating_target',heating_target)  
    return (heating_target + heating_target)/2  
  
  assert false 

     
     
       
fan = Fan(fan_gpio)
sc  = SmartCooling(args.sleep)
relay = RelayControl(relay_gpio, args.sleep, 300)

print('# overheat:' + str(args.overheat) + '°C mode:' + str(args.mode) + ' span:' + str(args.span) + '°C off:', args.off)
print('#1: date 2: time 3: temp house (°C) 4: temp collector (°C) 5: fan 6: temp outside (°C) 7: relay 8: target (°C)')

while True:
  now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
  sc.step() # sets states 

  house  = get_w1_temp(dev['house']) 
  collector = get_w1_temp(dev['collector'])
  outside = None

  speed = None
  
  # the target temperature can be quite complex function
  target = target_temp(house,args.mode)
  
  rs = -1 # relay state 
  rt = None
  
  try:
    outside = get_w1_temp(dev['outside'])
  except:
    pass # stays None, be careful not to interpret 0.0 as None! print('true' if 0.0 else 'false') -> false 
  
  if collector > args.overheat:
    speed = fan.set_duty_cylce(1000) # > 100 = switch on!
  elif args.off:
    # we don't want anything and we are not overheating
    speed = fan.set_duty_cylce(0)
  else:   
    # potential is the delta temperature (>= 0) we want act on, either cooling or heating. If large, much fan. If small, low or zero fan
    potential = 0
  
    if house < target: # too cold?
      potential = max(potential, collector - house) # usually compare with 0, active when collector > house
  
    if house > target: # too warm?
      potential = max(potential, house - collector) # usually compare with 0, active when house > collector
      # when collector > house but outside < house do only temporary testing to check for heat bubble in collector
      # outside might be colder than house but sun is heating - try to find close to sunset
      if outside is not None and outside < house: 
        # change nothing during testing or resting
        if sc.state == State.OPEN and collector > house: 
          sc.test() # switch to testing the test condition (o < h, c > h) is meet
        if sc.state == State.TESTING: # either just changed or already testing, do nothing when in state resting 
          potential = max(potential, house - outside) 
    
    want = min(100, min(potential,abs(house - target)) * 100/args.span) # 33 = 0 ... 100% within 3 C
    speed = fan.set_duty_cylce(want) # actually set speed
    
    # relay support for external cooling fan
    rs, rt = relay.set(house > target and outside is not None and outside + args.span < house) # sufficiently too warm and sufficiently cooler outside

  print(now, nice.format(house), nice.format(collector), frac.format(speed/100), nice.format(outside) if outside is not None else '-30', rs, target)    
  sys.stdout.flush() # make nohup redirect write the stuff now
  time.sleep(args.sleep)   

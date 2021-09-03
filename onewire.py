# return list of names of all w1 devices w/o eol
def get_w1_names(test = True):
  f = open("/sys/bus/w1/devices/w1_bus_master1/w1_master_slaves", "r")
  l = f.readlines() # can be read only once, reopen in case
  # contains eol ['28-011447b9faaa\n', '28-0114481419aa\n']
  r = []
  for s in l:
    name = s.rstrip()
    #if test:
    r.append(name) # remove eol
   
  return r     
  
  
# small helper which opens a w1_  
def open_w1_name(name):  
  f = open("/sys/bus/w1/devices/" + name + "/w1_slave", "r")
  return f
  
# give temperature by w1 name or list of w1 names in celcius (float)
def get_w1_temp(name):  
  if type(name) == list:
    res = []
    for n in name:
      res.append(get_w1_temp(n))
    return res
  else:    
    f = open_w1_name(name)
    l = f.readlines()
    # ['dc 01 4b 46 7f ff 0c 10 45 : crc=45 YES\n',  'dc 01 4b 46 7f ff 0c 10 45 t=29750\n']
    ts = l[-1].split()[-1] # 't=29750'
    t = float(ts[2:])/1000 # skip 't='
    return t

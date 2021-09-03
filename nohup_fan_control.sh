#!/bin/bash
nohup python3 ~/code/fan_control.py --mode heating > /mnt/ram/fan_control.dat 2>&1 &

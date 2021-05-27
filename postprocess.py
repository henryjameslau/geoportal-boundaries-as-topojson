import sys
import pandas as pd
import numpy as np
import geopandas as gpd
import json

# Simple script to show the parameters sent to the script, and generate a dummy file

if __name__ == "__main__":

    print("argv :", sys.argv)
    # open file
    file = open(sys.argv[1])
    areas=json.load(file)


    print(areas)
    # loop through areas
    for i in areas['services']:
        print(i)

    # close file
    file.close()

import sys
import pandas as pd
import numpy as np
import geopandas as gpd
import topojson as tp
import json

# Simple script to show the parameters sent to the script, and generate a dummy file

if __name__ == "__main__":

    print("argv :", sys.argv)
    # open file
    file = open(sys.argv[1])
    areas=json.load(file)


    # print(areas)
    # loop through areas
    for i in areas['services'][0]:
        geojson=gpd.read_file(i['url']+"/0/query?where=1%3D1&outFields=*&outSR=4326&f=json")
        topojson=tp.Topology(geojson).to_json('topojson.json')
        
    # close file
    file.close()

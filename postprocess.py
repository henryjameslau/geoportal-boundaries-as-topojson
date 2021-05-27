import sys
import geopandas as gpd
import topojson as tp
import requests

# Simple script to show the parameters sent to the script, and generate a dummy file

if __name__ == "__main__":

    print("argv :", sys.argv)
    # open file
    file = requests.get(sys.argv[1])
    areas=file.json()

    # loop through areas
    for i in areas['services'][0:1]:
        geojson=gpd.read_file(i['url']+"/0/query?where=1%3D1&outFields=*&outSR=4326&f=json")
        topo=tp.Topology(geojson)
        topo.to_json('topojson.json')

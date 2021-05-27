import sys
import geopandas as gpd
import topojson as tp
import json

# Simple script to show the parameters sent to the script, and generate a dummy file

if __name__ == "__main__":

    print("argv :", sys.argv)
    # open list of boundaries from the geoportal
    file = open(sys.argv[1])
    areas=json.load(file)

    # find only areas with BUC, BGC, BFE

    # loop through areas
    for i in areas['services'][0:1]:

        # name of boundary
        name=i['name']

        # read the file as a geojson
        geojson=gpd.read_file(i['url']+"/0/query?where=1%3D1&outFields=*&outSR=4326&f=json")

        # drop unnecessary fields
        # geojson=geojson.drop(columns=[''])
        # change projection
        # geojson = geojson.to_crs('EPSG:4326')

        pathtosave= 'outputs/'+name+'.json'
        # convert to topojson and save
        tp.Topology(geojson).to_json(pathtosave)

    # close file
    file.close()

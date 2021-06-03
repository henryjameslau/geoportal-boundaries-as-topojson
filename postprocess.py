import sys
import geopandas as gpd
import topojson as tp
import json
import re
import os

# Simple script to show the parameters sent to the script, and generate a dummy file

if __name__ == "__main__":

    print("argv :", sys.argv)
    # open list of boundaries from the geoportal
    file = open(sys.argv[1])
    areas=json.load(file)
    areas=areas['services']

    # find only areas with BUC, BGC
    # https://stackoverflow.com/questions/9012008/pythons-re-return-true-if-string-contains-regex-pattern
    regexp=re.compile(r'BUC|BGC|BSC')

    filtered=[]

    for s in areas:
        if regexp.search(s['name']):
            filtered.append([s['name'],s['url']])

    # loop through areas
    for i in filtered:

        # name of boundary
        print(i[0])

        # read the file as a geojson
        geojson=gpd.read_file(i[1]+"/0/query?where=1%3D1&outFields=*&outSR=4326&f=json")

        # Keep and rename the columns we want and drop any others
        for j in geojson.columns:
            if re.search(r'(NMW)',j):#drop any NMW
                geojson=geojson.drop(columns=[j])
            if re.search(r'CD',j):#look for fields with CD and rename it
                geojson=geojson.rename(columns={j:'AREACD'})
            if re.search(r'(NM)(?!NMW)',j):#look for NM but not NMW and rename it AREANM
                geojson=geojson.rename(columns={j:'AREANM'})
            if re.search(r'BNG',j):#drop any with BNG
                geojson=geojson.drop(columns=[j])
            if re.search(r'LAB',j):
                geojson=geojson.drop(columns=[j])
            if re.search(r'Shape__',j):#drop any with Shape__
                geojson=geojson.drop(columns=[j])
            if j=='LONG' or j=='LAT' or j=='OBJECTID':#drop any with long, lat, objectid
                geojson=geojson.drop(columns=[j])


        # change projection
        geojson = geojson.to_crs('EPSG:4326')

        #     make a folder if one doesn't exist
        os.makedirs('./outputs',exist_ok=True)

        pathtosave='./outputs/'+i[0]+'.json'
        # convert to topojson and save
        tp.Topology(geojson).to_json(fp=pathtosave)

    # close file
    file.close()

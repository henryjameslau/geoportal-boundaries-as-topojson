import sys
import geopandas as gpd
import topojson as tp
import json
import re

# Simple script to show the parameters sent to the script, and generate a dummy file

if __name__ == "__main__":

    print("argv :", sys.argv)
    # open list of boundaries from the geoportal
    file = open(sys.argv[1])
    areas=json.load(file)
    areas=areas['services']

    # find only areas with BUC, BGC, BFE
    # https://stackoverflow.com/questions/9012008/pythons-re-return-true-if-string-contains-regex-pattern
    # regexp=re.compile(r'BUC|BFC|BGC')
    regexp=re.compile(r'BUC')

    filtered=[]

    for s in areas:
        if regexp.search(s['name']):
            filtered.append([s['name'],s['url']])

    # loop through areas
    for i in filtered[0:5]:

        # name of boundary
        print(i[0])

        # read the file as a geojson
        geojson=gpd.read_file(i[1]+"/0/query?where=1%3D1&outFields=*&outSR=4326&f=json")

        # drop unnecessary fields
        geojson=geojson.drop(columns=['OBJECTID', 'BNG_E','BNG_N','LONG','LAT','Shape__Area', 'Shape__Length'])

        # find the names of the columns that have CD in or NM
        for j in geojson.columns:
            if re.search(r'CD',j):
                areacode=j
            if re.search(r'(NM)(?!NMW)',j):#look for NM but not NMW
                namecode=j

        #     Rename columns
        geojson=geojson.rename(columns={areacode:'AREACD',namecode:'AREANM'})
        # change projection
        geojson = geojson.to_crs('EPSG:4326')

        pathtosave=i[0]+'.json'
        # convert to topojson and save
        tp.Topology(geojson).to_json(pathtosave)

    # close file
    file.close()

import sys
import geopandas as gpd
import topojson as tp
import json
import re
import os
import requests

# Simple script to show the parameters sent to the script, and generate a dummy file

if __name__ == "__main__":

    print("argv :", sys.argv)
    # open list of boundaries from the geoportal
    file = open(sys.argv[1])
    areas=json.load(file)
    folders=areas['folders']

# blank array to store all the FeatureServers
    allFeatureServer=[]

    for i in folders:
        folder=requests.get("https://ons-inspire.esriuk.com/arcgis/rest/services/"+i+"?f=pjson")
        services=folder.json()['services']
        filtered=[service for service in services if service['type']=='FeatureServer']
        allFeatureServer=allFeatureServer+filtered
    #     https://devenum.com/filter-a-list-of-python-dictionaries-by-conditions/

    print(allFeatureServer)

    # find only areas with BUC, BGC
    # https://stackoverflow.com/questions/9012008/pythons-re-return-true-if-string-contains-regex-pattern
    regexp=re.compile(r'BUC|BGC|BSC')

    filtered=[]

    for s in allFeatureServer:
        if regexp.search(s['name']):
            filtered.append(s['name'])

    # loop through areas
    for i in filtered:

        # name of boundary
        print(i.split('/')[1])

        # read the file as a geojson
        geojson = gpd.read_file("https://ons-inspire.esriuk.com/arcgis/rest/services/"+i+"/FeatureServer/0/query?where=1%3D1&outFields=*&outSR=4326&f=json")


        # find the names of the columns that have CD in or NM
        for j in geojson.columns:
            if re.search(r'(nmw)',j):#drop any NMW
                geojson=geojson.drop(columns=[j])
            if re.search(r'cd',j):#look for fields with CD and rename it
                geojson=geojson.rename(columns={j:'AREACD'})
            if re.search(r'(nm)(?!nmw)',j):#look for NM but not NMW and rename it AREANM
                geojson=geojson.rename(columns={j:'AREANM'})
            if re.search(r'bng',j):#drop any with BNG
                geojson=geojson.drop(columns=[j])
            if re.search(r'lab',j):
                geojson=geojson.drop(columns=[j])
            if re.search(r'Shape_',j):#drop any with Shape__
                geojson=geojson.drop(columns=[j])
            if j=='long' or j=='lat' or j=='objectid' or j=='fid' or j=='role' or j=='lep':#drop any with long, lat, objectid
                geojson=geojson.drop(columns=[j])


        #     make a folder if one doesn't exist
        os.makedirs('./outputs',exist_ok=True)

        pathtosave='./outputs/'+i.replace("/","--")+'.json'
        # convert to topojson and save
        tp.Topology(geojson).to_json(fp=pathtosave)

    # close file
    file.close()

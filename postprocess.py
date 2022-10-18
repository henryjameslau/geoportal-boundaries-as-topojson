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
    loaded=json.load(file)
    folders=loaded['folders']

    areas=[]

    for i in folders:
        folder=requests.get("https://ons-inspire.esriuk.com/arcgis/rest/services/"+i+"?f=pjson")
        services=folder.json()['services']
        filtered=[service for service in services if service['type']=='FeatureServer']
    #     https://devenum.com/filter-a-list-of-python-dictionaries-by-conditions/
        areas=areas+filtered


    # find only areas with BUC, BGC
    # https://stackoverflow.com/questions/9012008/pythons-re-return-true-if-string-contains-regex-pattern
    regexp=re.compile(r'BUC|BGC|BSC')

    filtered=[]

    for s in areas:
        if regexp.search(s['name']):
            filtered.append(s['name'])

    for i in filtered:
        name=i.split('/')[1]
        info=requests.get('https://ons-inspire.esriuk.com/arcgis/rest/services/'+i+'/FeatureServer/?f=pjson')
        if info.status_code==200:
            info=info.json()
            url='https://ons-inspire.esriuk.com/arcgis/rest/services/'+i+'/FeatureServer/'+str(info['layers'][0]['id'])+'/query?where=1%3D1&outFields=*&outSR=4326&f=json'
            res=requests.get(url)
            geojson = gpd.read_file('https://ons-inspire.esriuk.com/arcgis/rest/services/'+i+'/FeatureServer/'+str(info['layers'][0]['id'])+'/query?where=1%3D1&outFields=*&outSR=4326&f=json')


            # find the names of the columns that have CD in or NM
            for j in geojson.columns:
                if re.search(r'(nmw)',j):#drop any NMW
                    geojson=geojson.drop(columns=[j])
                if re.search(r'cd',j):#look for fields with CD and rename it
                    geojson=geojson.rename(columns={j:'AREACD'})
                if re.search(r'(nm)(?!nmw)',j):#look for NM but not NMW and rename it AREANM
                    geojson=geojson.rename(columns={j:'AREANM'})
                if re.search(r'bng_e',j):#drop any with BNG_e
                    geojson=geojson.drop(columns=[j])
                if re.search(r'bng_n',j):#drop any with BNG_n
                    geojson=geojson.drop(columns=[j])
                if re.search(r'long',j):#drop any with long
                    geojson=geojson.drop(columns=[j])
                if re.search(r'lat',j):#drop any with lat
                    geojson=geojson.drop(columns=[j])
                if re.search(r'LAB',j):
                    geojson=geojson.drop(columns=[j])
                if re.search(r'Shape_',j):#drop any with Shape__
                    geojson=geojson.drop(columns=[j])
                if j=='OBJECTID':#drop any with long, lat, objectid
                    geojson=geojson.drop(columns=[j])

            # https://stackoverflow.com/questions/41959874/python-how-to-handle-folder-creation-if-folder-already-exists
            # make a folder
            os.makedirs('./outputs',exist_ok=True)

            # save as topojson
            tp.Topology(geojson).to_json(fp='./outputs/'+name+'.json')
            print("Success:"+name)
        else:
            print("Failed:"+name)

    # close file
    file.close()

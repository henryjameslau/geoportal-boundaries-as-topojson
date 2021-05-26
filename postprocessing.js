// This can be a typescript file as well

// Helper library written for useful postprocessing tasks with Flat Data
// Has helper functions for manipulating csv, txt, json, excel, zip, and image files
import { readJSON,writeJSON } from 'https://deno.land/x/flat@0.0.10/mod.ts'
// import mapshaper from 'https://cdn.skypack.dev/mapshaper';
import {topojson} from 'https://cdn.skypack.dev/topojson';


// Step 1: Read the downloaded_filename JSON
const filename = Deno.args[0] // Same name as downloaded_filename `const filename = 'btc-price.json';`
const json = await readJSON(filename)

// get a list of areas
const areas = json.services
console.log(areas[0])

//just pick the first area
const firstarea=areas[0].url+"/0/query?where=1%3D1&outFields=*&outSR=4326&f=json"
console.log(firstarea)

//get the first area as geojson
const firstGeojson = await readJSON(firstarea)
console.log(firstGeojson)
//convert it to topojson
// const firstTopo = topojson.topology(firstGeojson)

//write file
// await writeJSON('firstTopo.json',firstTopo)

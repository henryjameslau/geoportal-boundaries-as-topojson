# What does this do?
Uses [Github flat](https://octo.github.com/projects/flat-data) to automatically grab a load of geojson files every day from the [ONS Geoportal](https://geoportal.statistics.gov.uk/), stripping out unnecessary fields and renaming the code and name field as `AREANM` and `AREACD`, and converting them to topojson formats to be used with the [ONS' data visualisation team's map templates](https://github.com/ONSvisual/maptemplates).

Based off [`pierrotsmnrd`'s example](https://github.com/pierrotsmnrd/flat_data_py_example) of using python to run the postprocessing part. 

# Sounds great, but where are the topojsons
You can find the topojson files in the [outputs folder](https://github.com/henryjameslau/topojson/tree/main/outputs)

# NB
This doesn't have every boundary from the geoportal. Full extent files were too big so it's just generalised, super generalised and ultra generalised geographies.

# Contact
Anything wrong, open an issue or contact me on twitter ([@henrylau_ONS](https://twitter.com/henrylau_ONS))

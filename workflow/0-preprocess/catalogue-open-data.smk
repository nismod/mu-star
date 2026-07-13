"""Access open data catalogues

OpenData Mauritius CKAN Catalogue

See https://data.govmu.org/dataset/

OpenData Mauritius GeoNode

See https://geoportal.govmu.org
"""

rule ckan_download_metadata:
    output:
        json = "{data}/incoming/opendata_mauritius/catalogue.json"
    run:
        import json
        import requests
        url = "https://data.govmu.org/api/3/action/package_list"
        r = requests.get(url).json()
        package_ids = r["result"]
        packages = []
        for package_id in package_ids:
            r = requests.get(f"https://data.govmu.org/api/3/action/package_show?id={package_id}").json()
            packages.append(r["result"])
        with open(output.json, "w") as fh:
            json.dump(packages, fh)

rule ckan_tabular_metadata:
    input:
        json = "{data}/incoming/opendata_mauritius/catalogue.json"
    output:
        xlsx  = "{data}/incoming/opendata_mauritius/catalogue.xlsx"
    run:
        import json
        import pandas
        with open(input.json, "r") as fh:
            packages = json.load(fh)
        package_list = []
        package_record_list = []
        for p in packages:
            package_list.append({
                "id": p["id"],
                "name": p["name"],
                "notes": p["notes"],
                "organization.title": p["organization"]["title"],
                "organization.description": p["organization"]["description"],
                "metadata_created": p["metadata_created"],
                "metadata_modified": p["metadata_modified"],
                "state": p["state"],
            })
            for r in p["resources"]:
                package_record_list.append({
                    "package_id": p["id"],
                    "package_name": p["name"],
                    "id": r["id"],
                    "name": r["name"],
                    "url": r["url"],
                    "created": r["created"],
                    "last_modified": r["last_modified"],
                    "format": r["format"],
                    "hash": r["hash"],
                    "size": r["size"],
                })
        packages = pandas.DataFrame(package_list)
        package_records = pandas.DataFrame(package_record_list)
        with pandas.ExcelWriter(output.xlsx) as writer:
            packages.to_excel(writer, sheet_name="packages")
            package_records.to_excel(writer, sheet_name="records")


rule geonode_download_metadata:
    output:
        json = "{data}/incoming/opendata_mauritius/geonode_resources.json",
    run:
        import json
        import requests
        url = "https://geoportal.govmu.org/api/v2/resources.json"
        resources = []
        while True:
            r = requests.get(url).json()
            resources.extend(r["resources"])
            if r["links"]["next"] is not None:
                url = r["links"]["next"]
            else:
                break
        with open(output.json, "w") as fh:
            json.dump(resources, fh)

rule geonode_tabular_metadata:
    input:
        json = "{data}/incoming/opendata_mauritius/geonode_resources.json"
    output:
        xlsx  = "{data}/incoming/opendata_mauritius/geonode_resources.xlsx"
    run:
        import json
        import pandas
        with open(input.json, "r") as fh:
            resources = json.load(fh)
        resource_list = []
        for p in resources:
            resource_list.append({
                "uuid": p["uuid"],
                "pk": p["pk"],
                "alternate": p["alternate"],
                "title": p["title"],
                "abstract": p["abstract"],
                "attribution": p["attribution"],
                "license.identifier": p["license"]["identifier"],
                "date": p["date"],
                "date_type": p["date_type"],
                "created": p["created"],
                "last_updated": p["last_updated"],
                "download_url": p["download_url"],
            })
        resources = pandas.DataFrame(resource_list)
        with pandas.ExcelWriter(output.xlsx) as writer:
            resources.to_excel(writer, sheet_name="resources")

"""Running the BHM model setup script.
Adjust the relevant variables as desired.
    Centralised model setup
        - This script centralises the process of creating and uploading a model to the cluster
        - The ambition is to simplify the parameter initialisation,
            automate where possible the definition of some of them,
            mainly related to the resource allocation.
            And finally produce a runnable model to be uploaded to the cluster.
"""

from economy import CATALOGUE_ROOT, OUTPUT_ROOT

rule econ_model_setup:
    """Run the BHM dispatcher for one tracked Mauritius model-run directory."""
    output:
        model_run=directory(f"{OUTPUT_ROOT}/{{model_run}}"),
    params:
        output_root=OUTPUT_ROOT,
        catalogue_root=CATALOGUE_ROOT,
    run:

        import scalenav.oop as snoo
        import os
        import datetime
        import yaml
        import ibis as ib
        from ibis import _
        import numpy as np


        # In[2]:


        from globdata import ISIC_CODES, REGION_INDEX
        from globdata.base_constraints import ModelConstraint
        from globdata.base_params import ModelParams
        from globdata.parameters import job_partition, load_catalogue, set_model_index

        ib.options.interactive = True
        ib.options.graphviz_repr = True


        conn = snoo.connect(
            memory_limit="15GB",
            max_temp_directory_size="15GB",
        )


        REMOTE_ROOT = "/data/ouce-opsis/cenv1069/global-econ-model/py"

        LOCAL_ROOT = "/Users/cenv1069/Documents/global-econ/model/RESULTS"

        CATALOGUE = load_catalogue(local=True,root="../../catalogue/")

        OUTPUT_ROOT = params.output_root


        PROXY_LAYERS = [
            # "jrc_s_nres_10",
            # "copernicus_builtup_100m_2018",
            # "jrc_pop_100",
        ]


        PROXY_TO_SECTOR : dict = {
            # "A" : {'copernicus_cropland_100m_2018'}, # *PROXY_LAYERS
            # "B" : {'maus_mining'},
            "B" : {},
            "C" : {*PROXY_LAYERS},
            "D" : {*PROXY_LAYERS},
            "E" : {*PROXY_LAYERS},
            "F" : {*PROXY_LAYERS},
            "G" : {*PROXY_LAYERS},
            "H" : {*PROXY_LAYERS},
            "I" : {*PROXY_LAYERS},
            "J" : {*PROXY_LAYERS},
            "K" : {*PROXY_LAYERS},
            "L" : {*PROXY_LAYERS},
            "M" : {*PROXY_LAYERS},
            "N" : {*PROXY_LAYERS},
            "O" : {*PROXY_LAYERS},
            "P" : {*PROXY_LAYERS},
            "Q" : {*PROXY_LAYERS},
            "R" : {*PROXY_LAYERS},
            "S" : {*PROXY_LAYERS},
            "T" : {*PROXY_LAYERS},
            "U" : {*PROXY_LAYERS},
        }


        # ## Starting point

        # ## Watch FOR
        # - model name
        # - region
        # - time
        # - memory
        # - samples
        # - model resolution
        # - industries


        MIRROR : bool = True # this is not relevant in the smk workflow keep False


        model_nickname = f"" # if desired instead of region name

        region = [
            "Mauritius",
        ]


        # automate the next parameters in the short term
        time : str = "15:00:00"
        memory : int = 50
        RUN : bool = False
        samples : int = 1_000
        final_res : int = 8


        constraints = ModelConstraint(
            constraint_file = 'mus_gdp2024',
            custom_sections_agg = {},
            search_columns = [
                "gid_0",
                "country",
                "continent",
                'sub_continent',
            ],
            industries_to_predict = [
                # 'A',
                # 'C',
                # 'D',
                # 'E',
                # 'F',
                # 'G',
                # 'H',
                # 'I',
                # 'J',
                # 'K',
                # 'L',
                # 'M',
                # 'N',
                # 'O',
                # 'P',
                # 'Q',
                # 'R',
                # 'S',
            ],
            constraint_id_col = 'gid_0', # "geo_fips"
            proxy_layer_names = PROXY_LAYERS,
            proxy_to_sector = PROXY_TO_SECTOR,
        )

        # constraints =  ModelConstraint(
        #     custom_sections_agg = {},
        #     search_columns = [
        #         'gid_0',
        #         'gid_1',
        #         "country",
        #         'region',
        #         'sub_continent',
        #         'continent',
        #     ],
        #     industries_to_predict = [
        #         # "agriculture_usd_2015",
        #         "manufacturing_usd_2015",
        #         "services_usd_2015",
        #     ], # sectors
        #     constraint_file = 'dose_wdi_v7',
        #     constraint_id_col = 'gid_1', # "geo_fips"
        #     dose_types = {
        #         "agriculture_usd_2015" : "A",
        #         "manufacturing_usd_2015" : "B-F",
        #         "services_usd_2015" : "G-S",
        #     },
        #     proxy_to_sector = PROXY_TO_SECTOR,
        #     proxy_layer_names = PROXY_LAYERS,
        # )

        # constraints = ModelConstraint(
        #     industries_to_predict = [
        #         #### mining
        #         # 'B',
        #         ## manuf
        #         "C",
        #         # "D",
        #         # "E",
        #         # 'F',
        #         ### serv 1
        #         'G',
        #         # "H",
        #         # "I",
        #         # 'J',
        #         # "K",
        #         # "L",
        #         # "M",
        #         # ### serv 2
        #         # "N",
        #         # "O",
        #         # 'P',
        #         # 'Q',
        #         # 'R',
        #         # "S",
        #     ],
        #     constraint_file = 'gloria_sections_2015',
        #     custom_sections_agg = {},
        #     search_columns = [
        #         "gid_0",
        #         "country",
        #         "continent",
        #         'sub_continent',
        #     ],
        #     constraint_id_col = 'gid_0', # "geo_fips"
        #     proxy_layer_names = PROXY_LAYERS,
        #     proxy_to_sector = PROXY_TO_SECTOR,
        # )


        # In[11]:


        verbose : bool = True

        pass_ : str = "no-pass" # "pass" #
        n_cores : int = 6
        target_accept : float = 0.97
        max_treedepth : int = 15
        tunes : int = int(np.max([450, samples*.1]))
        trace_frac : float = 0.3
        information_threshold : float = .98

        partition : str = job_partition(time) # short, medium, long
        # constraints
        # alpha =


        # ## READ CONSTRAINT

        # In[12]:


        constraint_file = constraints.constraint_file


        # In[13]:


        # read filename from parameters.py
        dose = snoo.table(
            conn,
            name = "dose",
            path = CATALOGUE[constraint_file],
            overwrite = True,
        )

        dose_count = dose.count().execute()


        # In[14]:


        search_columns = constraints.search_columns
        constraint_id_col = constraints.constraint_id_col

        looked_up = (
            dose
            .pipe(
                region_lookup,
                value=region,
                columns=search_columns,
            )
            .order_by(constraint_id_col)
        )
        looked_up.select(
            "id",
            *search_columns,
            "size",
            "job_id",
            'path',
            "geometry"
        ).execute()


        # In[15]:


        # looked_up #.select("gid_0").execute()


        # In[16]:


        # looked_up["geometry"].execute()


        # In[17]:


        # # # trying to estimate memory use and time.

        # memory_estimates = []

        # geoms = looked_up["geometry"].execute()

        # # for geo in geoms:
        # #     print(type(geo))

        # for geom in geoms.tolist():

        #     bbox = geom.bounds

        #     loc_count = (
        #         snoo.table(
        #             conn,
        #             name="copernic",
        #             path=CATALOGUE['copernicus_builtup_100m_2018'],
        #             bbox=bbox,
        #             overwrite=True,
        #         )
        #         .pipe(
        #             snoo.project,
        #             res=final_res,
        #         )
        #         .group_by("h3_id")
        #         .agg(
        #             val = ib.literal(1)
        #         )
        #         .pipe(
        #             snoo.add_centr
        #         )
        #         # .filter(
        #         #     _['geom'].intersects(geom)
        #         # )
        #         .count()
        #     ).execute()

        #     # print(loc_count)
        #     mem, _ = mem_use(
        #         total_res=loc_count,
        #         samples=samples,
        #         tunes=tunes,
        #         trace_frac=.3,
        #         chains=n_cores,
        #         n_sectors=len(constraints.industries_to_predict),
        #         _nice=True,
        #     )

        #     memory_estimates.append(mem)

        # print(memory_estimates)


        # In[18]:


        # layers = snoo.combine(
        #     conn,
        #     input={
        #         "copernic" : copernic,
        #         "places" : places,
        #     },
        #     name = "layers",
        # )


        # In[19]:


        # (
        #     looked_up
        #     .pipe(
        #         snoo.dump_fill_h3,
        #         res=5,
        #     )
        #     .select("h3_id")
        #     .unnest("h3_id")
        #     .distinct(on="h3_id")
        # )


        # In[20]:


        if looked_up.count().execute()==0:
            raise Warning("NOT FOUND")


        # In[21]:


        # region_name = [reg.strip().lower().replace(" ","-") for reg in region]
        # region_name = region.strip().lower().replace(" ","_")


        # In[22]:


        model_name : str = model_alias(
            model_nickname=model_nickname,
            study_region=region,
            final_res=final_res,
            **constraints.__dict__,
        )

        print(model_name)
        model_dir = f"{model_name}"
        # print(model_dir)


        # In[23]:


        study_region = looked_up[constraint_id_col].execute().values.tolist()
        print(len(study_region))


        # In[24]:


        # HERE CREATE THE option to create an index out of a set of existring jobs by taking the ones that did not work.


        # In[25]:


        job_index = set_model_index(
            study_region,
            constraints,
        )


        # In[26]:


        job_index


        # In[27]:


        # import collections as cols
        # cols.
        # study_region


        # In[28]:


        params = ModelParams(
            model_nickname=model_nickname,
            model_name = model_name,
            study_region = study_region,
            final_res = final_res,
            verbose = verbose,
            n_cores = n_cores,
            samples = samples,
            chains = n_cores,
            target_accept = target_accept,
            max_treedepth = max_treedepth,
            tunes = int(samples*.3),
            # trace_frac = 0.3,
            information_threshold = information_threshold,
        )


        # In[29]:


        # BASH option

        # model_name : str = ""
        region_param = " ".join([f'"{val}"' for val in study_region])

        local_model_dir = f"../RESULTS/{model_dir}"
        mirror_model_dir = f"../py/BHM/{model_dir}"

        job_index_path = f"{local_model_dir}/job_idx.csv"
        job_index_mirror_path = f"{mirror_model_dir}/job_idx.csv"

        yaml_params = f"{local_model_dir}/model_params.yaml"
        yaml_mirror_params = f"{mirror_model_dir}/model_params.yaml"

        out_out : str = f"{OUTPUT_ROOT}/{model_dir}/logs/{model_name}_%A_%a.out"
        out_error : str = f"{OUTPUT_ROOT}/{model_dir}/logs/{model_name}_%A_%a.err"
        # array_size : int = int(looked_up.size.execute()[0])
        array_size : int = int(len(job_index))
        cpus_per_task : int = params.n_cores


        # In[30]:


        array_str = "1" if array_size==1 else f"1-{array_size}"
        # array_str


        # In[31]:


        sbatch = f"""#! /bin/bash
        #SBATCH --job-name={model_name}
        #SBATCH --array={array_str}
        #SBATCH --clusters=arc
        #SBATCH --cpus-per-task={cpus_per_task}
        #SBATCH --time={time}
        #SBATCH --mem={memory}G
        #SBATCH --partition={partition}
        #SBATCH --output={out_out} # out_files/bhm_run_array_%A_%a.out
        #SBATCH --error={out_error}
        #SBATCH --mail-type=END,FAIL
        #SBATCH --mail-user=ivann.schlosser@ouce.ox.ac.uk

        source ~/.bashrc
        module load Python/3.12.3-GCCcore-13.3.0
        micromamba activate global-data-2

        REG_ID=$((SLURM_ARRAY_TASK_ID - 1))

        echo $REG_ID

        python {REMOTE_ROOT}/run_model_cl.py \\
            {region_param} \\
            --reg_id $REG_ID \\
            --{pass_} \\
            --output={model_dir}

        micromamba deactivate
        module unload Python/3.12.3-GCCcore-13.3.0
        """


        # In[32]:


        print(sbatch)


        # ### Saving parameters

        # In[33]:


        if not MIRROR:
            if os.path.exists(local_model_dir):
                # raise Warning("This model exists, if you run this again, you might erase data.")
                pass

            os.mkdir(local_model_dir)
            os.mkdir(f"{local_model_dir}/logs")

            with open(f"{local_model_dir}/modelling_run_array.sh", "w") as f:
                f.write(sbatch)

            with open(yaml_params, "w") as f:
                yaml.safe_dump({**params.__dict__, **constraints.__dict__}, f, sort_keys=False)

            job_index.to_csv(job_index_path,index=False)

        elif MIRROR:
            os.mkdir(mirror_model_dir)

            with open(f"{mirror_model_dir}/modelling_run_array.sh", "w") as f:
                f.write(sbatch)

            with open(yaml_mirror_params, "w") as f:
                yaml.safe_dump({**params.__dict__, **constraints.__dict__}, f, sort_keys=False)

            job_index.to_csv(job_index_mirror_path,index=False)


        # In[34]:


        print(model_dir)


        # ### Run localy if desired

        # In[35]:


        # if MIRROR:
        #     os.system("""
        #         micromamba activate global-data-2
        #         python ../py/run_model_cl.py \\
        #             {region_param} \\
        #             --reg_id $REG_ID \\
        #             --{pass_} \\
        #             --output={model_dir}
        #     """)


        # # Remote model

        # In[36]:


        REMOTE_ROOT


        # In[37]:


        if not MIRROR:
            # update from github
            os.system(f'ssh arc "cd {REMOTE_ROOT} && git pull"')
            # move things there
            os.system(f"scp -r {local_model_dir}/ arc:{REMOTE_ROOT}/{OUTPUT_ROOT}/{model_dir}")

            if RUN:
                # send job
                os.system(f'ssh arc "cd {REMOTE_ROOT} && sbatch {OUTPUT_ROOT}/{model_dir}/modelling_run_array.sh"')


        # In[ ]:

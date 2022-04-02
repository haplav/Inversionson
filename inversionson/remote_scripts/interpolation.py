import multi_mesh.api
import sys
import toml
import os
import shutil
import pathlib

# Here we should handle all the looking at the different mesh folders.
# If the mesh does not exist on scratch, we check on non-scratch.
# The from_mesh also needs to be found on either one of the two.
# This needs to be implemented on Monday/Saturday/Tuesday


def create_mesh(mesh_info, source_info):
    mesh_location = os.path.join(
        mesh_info["mesh_folder"], mesh_info["event_name"], "mesh.h5"
    )
    long_term_mesh_location = os.path.join(
        mesh_info["long_term_mesh_folder"], mesh_info["event_name"], "mesh.h5"
    )
    if os.path.exists(mesh_location):
        print("Mesh already exists, copying it to here")
        shutil.copy(mesh_location, "./to_mesh.h5")
        return
    elif os.path.exists(long_term_mesh_location):
        print("Mesh already exists, copying it to here")
        shutil.copy(long_term_mesh_location, "./to_mesh.h5")
        return
    else:
        from salvus.mesh.simple_mesh import SmoothieSEM

        sm = SmoothieSEM()
        sm.basic.model = "prem_ani_one_crust"
        sm.basic.min_period_in_seconds = float(mesh_info["min_period"])
        sm.basic.elements_per_wavelength = 1.7
        sm.basic.number_of_lateral_elements = int(mesh_info["elems_per_quarter"])
        sm.advanced.tensor_order = 4
        if "ellipticity" in mesh_info.keys():
            sm.spherical.ellipticity = float(mesh_info["ellipticity"])
        if "ocean_loading" in mesh_info.keys():
            sm.ocean.bathymetry_file = pathlib.Path(
                mesh_info["ocean_loading"]["remote_file"]
            )
            sm.ocean.bathymetry_varname = mesh_info["ocean_loading"]["variable"]
            sm.ocean.ocean_layer_style = "loading"
            sm.ocean.ocean_layer_density = 1025.0
        if "topography" in mesh_info.keys():
            sm.topography.topography_file = pathlib.Path(
                mesh_info["topography"]["use"]["remote_file"]
            )
            sm.topography.topography_varname = mesh_info["topography"]["use"][
                "variable"
            ]
        sm.source.latitude = float(source_info["latitude"])
        sm.source.longitude = float(source_info["longitude"])
        sm.refinement.lateral_refinements.append(
            {"theta_min": 40.0, "theta_max": 140.0, "r_min": 6250.0}
        )
        m = sm.create_mesh()
        m.write_h5("to_mesh.h5", mode="all")


def get_standard_gradient(mesh_info):
    remote_gradient = (
        pathlib.Path(mesh_info["mesh_folder"]) / "standard_gradient" / "mesh.h5"
    )

    shutil.copy(remote_gradient, "./to_mesh.h5")


def move_mesh(mesh_folder, event_name):
    mesh_location = pathlib.Path(mesh_folder) / event_name / "mesh.h5"
    if not os.path.exists(mesh_location):
        if not os.path.exists(mesh_location.parent):
            os.makedirs(mesh_location.parent)
        print("Copying mesh for storage")
        shutil.copy("./output/mesh.h5", mesh_location)


def interpolate_fields(from_mesh, to_mesh, layers, parameters, stored_array=None):
    multi_mesh.api.gll_2_gll_layered_multi(
        from_mesh,
        to_mesh,
        nelem_to_search=20,
        layers=layers,
        parameters=parameters,
        stored_array=stored_array,
    )


def create_simulation_object(mesh_info, source_info, receiver_info, simulation_info):
    """
    Create the simulation object remotely and write it into a dictionary toml file.
    This dictionary is then downloaded and used locally to create the simulation object,
    bypassing the problem of slow receiver placements.

    The inputs are all dictionaries with the relevant information needed for
    the creation of the simulation object.
    """
    import salvus.flow.simple_config as sc

    receivers = [
        sc.receiver.seismology.SideSetPoint3D(
            latitude=rec["latitude"],
            longitude=rec["longitude"],
            network_code=rec["network-code"],
            station_code=rec["station-code"],
            depth_in_m=0.0,
            fields=["displacement"],
            side_set_name="r1",
        )
        for rec in receiver_info
    ]

    src = sc.source.seismology.SideSetMomentTensorPoint3D(
        latitude=source_info["latitude"],
        longitude=source_info["longitude"],
        depth_in_m=source_info["depth_in_m"],
        mrr=source_info["mrr"],
        mtt=source_info["mtt"],
        mpp=source_info["mpp"],
        mtp=source_info["mtp"],
        mrp=source_info["mrp"],
        mrt=source_info["mrt"],
        side_set_name=source_info["side_set"],
        source_time_function=sc.stf.Custom(
            filename=f"REMOTE:{source_info['stf']}", dataset_name="/source"
        ),
    )

    mesh = pathlib.Path().resolve() / "output" / "mesh.h5"
    w = sc.simulation.Waveform(mesh=mesh, sources=src, receivers=receivers)

    w.physics.wave_equation.end_time_in_seconds = simulation_info["end_time"]
    w.physics.wave_equation.time_step_in_seconds = simulation_info["time_step"]
    w.physics.wave_equation.start_time_in_seconds = simulation_info["start_time"]
    w.physics.wave_equation.attenuation = simulation_info["attenuation"]

    bound = False
    boundaries = []
    if simulation_info["absorbing_boundaries"]:
        print("I think there are absorbing boundaries")
        bound = True
        absorbing = sc.boundary.Absorbing(
            width_in_meters=simulation_info["absorbing_boundary_length"],
            side_sets=simulation_info["side_sets"],
            taper_amplitude=1.0 / simulation_info["minimum_period"],
        )
        boundaries.append(absorbing)

    if "ocean_loading" in mesh_info.keys():
        print("I think there is ocean loading")
        bound = True
        ocean_loading = sc.boundary.OceanLoading(side_sets=[source_info["side_set"]])
        boundaries.append(ocean_loading)
    if bound:
        w.physics.wave_equation.boundaries = boundaries
    w.output.volume_data.format = "hdf5"
    w.output.volume_data.filename = "output.h5"
    w.output.volume_data.fields = ["adjoint-checkpoint"]
    w.output.volume_data.sampling_interval_in_time_steps = "auto-for-checkpointing_20"
    w.validate()

    with open("output/simulation_dict.toml", "w") as fh:
        toml.dump(w.get_dictionary(), fh)


if __name__ == "__main__":
    """
    Call with python name_of_script toml_filename
    """
    toml_filename = sys.argv[1]

    info = toml.load(toml_filename)
    mesh_info = info["mesh_info"]
    if not info["gradient"]:
        source_info = info["source_info"]
        receiver_info = info["receiver_info"]
        simulation_info = info["simulation_info"]
        create_mesh(mesh_info=mesh_info, source_info=source_info)
        print("Mesh created or already existed")
    else:
        get_standard_gradient(mesh_info=mesh_info)
    interpolate_fields(
        from_mesh="./from_mesh.h5",
        to_mesh="./to_mesh.h5",
        layers="nocore",
        parameters=["VPV", "VPH", "VSV", "VSH", "RHO"],
    )
    print("Fields interpolated")
    shutil.move("./to_mesh.h5", "./output/mesh.h5")
    if not info["gradient"]:
        move_mesh(
            mesh_folder=mesh_info["mesh_folder"], event_name=mesh_info["event_name"]
        )
        print("Meshed moved to longer term storage")
        print("Creating simulation object")
        create_simulation_object(mesh_info, source_info, receiver_info, simulation_info)

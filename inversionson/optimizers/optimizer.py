"""
Base optimization class. It covers all the basic things that most optimizers
have in common. The class serves the purpose of making it easy to add
a custom optimizer to Inversionson. Whenever the custom optimizer has a 
task which works the same as in the base class. It should aim to use that one.
"""
import sys
from abc import abstractmethod as _abstractmethod
from pathlib import Path
import os
import glob
import h5py
import toml
from typing import List, Union
from salvus.flow.api import get_site
from inversionson import InversionsonError
from inversionson.helpers import autoinverter_helpers as helpers


class Optimize(object):

    # Derived classes should add to this
    available_tasks = [
        "prepare_iteration",
        "run_forward",
        "compute_misfit",
        "compute_validation_misfit",
        "compute_gradient",
        "regularization",
        "update_model",
        "documentation",
    ]

    # Derived classes should override this
    optimizer_name = "BaseClass for optimizers. Don't instantiate. If you see this..."

    def __init__(self, comm):

        # This init is only called by derived classes

        self.current_task = self.read_current_task()

        self.comm = comm
        self.opt_folder = (
            Path(self.comm.project.paths["inversion_root"]) / "OPTIMIZATION"
        )

        self.parameters = self.comm.project.inversion_params
        if not os.path.exists(self.opt_folder):
            os.mkdir(self.opt_folder)

        # These folders are universally needed
        self.model_dir = self.opt_folder / "MODELS"
        self.task_dir = self.opt_folder / "TASKS"
        self.raw_gradient_dir = self.opt_folder / "RAW_GRADIENTS"
        self.raw_update_dir = self.opt_folder / "RAW_UPDATES"
        self.regularization_dir = self.opt_folder / "REGULARIZATION"

        # Do any folder initilization for the derived classes
        self._initialize_derived_class_folders()

        self.config_file = self.opt_folder / "opt_config.toml"

        if not os.path.exists(self.config_file):
            self._write_initial_config()
            print(
                f"Please set config and provide initial model to "
                f"{self.optimizer_name} optimizer in {self.config_file} \n"
                f"Then reinitialize the {self.optimizer_name} optimizer."
            )
            sys.exit()
        self._read_config()

        if self.initial_model == "":
            print(
                f"Please set config and provide initial model to "
                f"{self.optimizer_name} optimizer in {self.config_file} \n"
                f"Then reinitialize the {self.optimizer_name} optimizer."
            )
            sys.exit()

        # Initialize folders if needed
        if not os.path.exists(self._get_path_for_iteration(0, self.model_path)):
            if self.initial_model is None:
                raise InversionsonError(
                    f"{self.optimizer_name} needs to be initialized with a "
                    "path to an initial model."
                )
            print(f"Initializing {self.optimizer_name}...")
            self._init_directories()
            self._issue_first_task()
        self.tmp_model_path = self.opt_folder / "tmp_model.h5"
        self._read_task_file()

        # Once this exits, continue with the derived class __init__().

    def print(
        self,
        message: str,
        color: str = "magenta",
        line_above: bool = False,
        line_below: bool = False,
        emoji_alias: Union[str, List[str]] = ":chart_with_downwards_trend:",
    ):
        self.comm.storyteller.printer.print(
            message=message,
            color=color,
            line_above=line_above,
            line_below=line_below,
            emoji_alias=emoji_alias,
        )

    @_abstractmethod
    def _initialize_derived_class_folders(self):
        """You need to make this yourself. Can do nothing, if no extra folders are
        required"""
        pass

    @_abstractmethod
    def _init_directories(self):
        pass

    @_abstractmethod
    def _issue_first_task(self):
        pass

    @_abstractmethod
    def _read_task_file(self):
        pass

    def _write_initial_config(self):
        """
        Writes the initial config file.
        """
        config = {
            "step_length": 0.001,
            "parameters": ["VSV", "VSH", "VPV", "VPH"],
            "initial_model": "",
            "max_iterations": 1000,
        }
        with open(self.config_file, "w") as fh:
            toml.dump(config, fh)

        print(
            "Wrote a config file for the Base optimizer. Please provide "
            "an initial model."
        )

    def _read_config(self):
        """Reads the config file."""

        if not os.path.exists(self.config_file):
            raise Exception("Can't read the ADAM config file")
        config = toml.load(self.config_file)
        self.initial_model = config["initial_model"]
        self.step_length = config["step_length"]
        if "max_iterations" in config.keys():
            self.max_iterations = config["max_iterations"]
        else:
            self.max_iterations = None
        self.parameters = config["parameters"]

    def read_current_task(self):
        """
        Read the current task from file
        """
        pass

    @property
    def iteration_number(self):
        "Returns the number of the newest iteration"
        return max(self.find_iteration_numbers())

    @property
    def iteration_name(self):
        return f"model_{self.iteration_number:05d}"

    @property
    def model_path(self):
        return self.model_dir / f"model_{self.iteration_number:05d}.h5"

    def find_iteration_numbers(self):
        models = glob.glob(f"{self.model_dir}/*.h5")
        if len(models) == 0:
            return [0]
        iteration_numbers = []
        for model in models:
            iteration_numbers.append(int(model.split(".")[0].split("_")[-1]))
        return iteration_numbers

    def delete_remote_files(self):
        self.comm.salvus_flow.delete_stored_wavefields(self.iteration_name, "forward")
        self.comm.salvus_flow.delete_stored_wavefields(self.iteration_name, "adjoint")

        if self.comm.project.meshes == "multi-mesh":
            self.comm.salvus_flow.delete_stored_wavefields(
                self.iteration_name, "prepare_forward"
            )
            self.comm.salvus_flow.delete_stored_wavefields(
                self.iteration_name, "gradient_interp"
            )
        if self.comm.project.hpc_processing:
            self.comm.salvus_flow.delete_stored_wavefields(
                self.iteration_name, "hpc_processing"
            )

    def prepare_iteration(
        self,
        it_name: str = None,
        move_meshes: bool = False,
        first_try: bool = True,
        events: List[str] = None,
    ):
        """
        A base function for preparing iterations

        :param it_name: Name of iteration, will use autoname if None is passed, defaults to None
        :type it_name: "str", optional
        :param move_meshes: Do meshes need to be moved to remote, defaults to False
        :type move_meshes: bool, optional
        :param first_try: Only change in trust region methods if region is being reduced, defaults to True
        :type first_try: bool, optional
        :param events: Pass a list of events if you want them to be predefined, defaults to None
        :type events: List[str], optional
        """
        it_name = self.iteration_name if it_name is None else it_name
        self.comm.project.change_attribute("current_iteration", it_name)
        validation = "validation" in it_name
        print("preparing iteration for ", it_name)
        if self.comm.lasif.has_iteration(it_name):
            raise InversionsonError(f"Iteration {it_name} already exists")

        if events is None and not validation:
            events = self.comm.lasif.list_events()
        elif events is None and validation:
            events = self.comm.project.validation_dataset
        self.comm.lasif.set_up_iteration(it_name, events)
        self.comm.project.create_iteration_toml(it_name)
        self.comm.project.get_iteration_attributes(validation)

        if self.comm.project.meshes == "multi-mesh" and move_meshes:
            if self.comm.project.interpolation_mode == "remote":
                interp_site = get_site(self.comm.project.interpolation_site)
            else:
                interp_site = None
            self.comm.multi_mesh.add_fields_for_interpolation_to_mesh()
            self.print(
                f"Moving mesh to {self.comm.project.interpolation_site}",
                emoji_alias=":package:",
            )
            self.comm.lasif.move_mesh(
                event=None, iteration=it_name, hpc_cluster=interp_site
            )
        elif self.comm.project.meshes == "mono-mesh" and move_meshes:
            self.print(
                f"Moving mesh to {self.comm.project.interpolation_site}",
                emoji_alias=":package:",
            )
            self.comm.lasif.move_mesh(event=None, iteration=it_name)
        self.print(
            f"Uploading source time function to {self.comm.project.site_name}",
            emoji_alias=":package:",
        )
        self.comm.lasif.upload_stf(iteration=it_name)

    def run_forward(self, verbose: bool = False):
        """
        Dispatch the forward simulations for all events

        :param verbose: You want to know the details?, defaults to False
        :type verbose: bool, optional
        """
        self.forward_helper = helpers.ForwardHelper(
            comm=self.comm, events=self.comm.project.events_in_iteration
        )
        self.forward_helper.dispatch_forward_simulations(verbose=verbose)
        assert self.forward_helper.assert_all_simulations_dispatched()

    def select_new_windows(self):
        """
        Some logic that decides if new windows need to be selected or not.

        NOT FINISHED (OBVIOUSLY)
        """
        return True

    def compute_validation_misfit(self, verbose: bool = False):
        """
        Compute misfits for validation dataset
        """
        if verbose:
            print("Creating average mesh for validation")
        if self.iteration_number != 0:
            to_it = self.iteration_number
            from_it = self.iteration_number - self.comm.project.when_to_validate + 1
            self.comm.salvus_mesher.get_average_model(iteration_range=(from_it, to_it))
            self.comm.multi_mesh.add_fields_for_interpolation_to_mesh()
            if self.comm.project.interpolation_mode == "remote":
                self.comm.lasif.move_mesh(
                    event=None,
                    iteration=None,
                    validation=True,
                )
        val_forward_helper = helpers.ForwardHelper(
            self.comm, self.comm.project.validation_dataset
        )
        assert "validation_" in self.comm.project.current_iteration
        val_forward_helper.dispatch_forward_simulations(verbose=verbose,
                                                        validation=True)
        assert val_forward_helper.assert_all_simulations_dispatched()
        val_forward_helper.retrieve_forward_simulations(
            adjoint=False, verbose=verbose, validation=True
        )
        val_forward_helper.report_total_validation_misfit()

    def compute_misfit(
        self,
        adjoint: bool = True,
        window_selection: bool = None,
        verbose: bool = False,
    ):
        """
        Retrieve and process the results of the forward simulations. Compute the misfits
        between synthetics and data.

        :param adjoint: Directly submit adjoint simulation, defaults to True
        :type adjoint: bool, optional
        :param window_selection: If windows should definitely be selected, if
            this is not clear, leave it at None. defaults to None
        :type window_selection: bool, optional
        :param verbose: You want to know the details?, defaults to False
        :type verbose: bool, optional
        """
        if window_selection is None:
            window_selection = self.select_new_windows()
        self.forward_helper = helpers.ForwardHelper(
            comm=self.comm, events=self.comm.project.events_in_iteration
        )
        self.forward_helper.retrieve_forward_simulations(
            adjoint=adjoint, windows=window_selection, verbose=verbose
        )
        assert self.forward_helper.assert_all_simulations_retrieved()

    def compute_gradient(self, verbose=False):
        """
        Submit adjoint simulations to compute gradients.

        :param verbose: Do we want the details?, defaults to False
        :type verbose: bool, optional
        """

        self.adjoint_helper = helpers.AdjointHelper(
            comm=self.comm, events=self.comm.project.events_in_iteration
        )
        self.adjoint_helper.dispatch_adjoint_simulations(verbose=verbose)
        assert self.adjoint_helper.assert_all_simulations_dispatched()

    def regularization(self):
        """
        To be implemented
        """
        pass

    def update_model(self):
        """
        Not yet implemented for the standard optimization.
        """
        pass

    def get_parameter_indices(self, filename):
        """Get parameter indices in h5 file"""
        with h5py.File(filename, "r") as h5:
            h5_data = h5["MODEL/data"]
            # Get dimension indices of relevant parameters
            # These should be constant for all gradients, so this is only done
            # once.
            dim_labels = h5_data.attrs.get("DIMENSION_LABELS")[1][1:-1]
            if not type(dim_labels) == str:
                dim_labels = dim_labels.decode()
            dim_labels = dim_labels.replace(" ", "").split("|")
            indices = []
            for param in self.parameters:
                indices.append(dim_labels.index(param))
        return indices

    def get_h5_data(self, filename):
        """
        Returns the relevant data in the form of ND_array with all the data.
        """
        indices = self.get_parameter_indices(filename)

        with h5py.File(filename, "r") as h5:
            data = h5["MODEL/data"][:, :, :].copy()
            return data[:, indices, :]

    def set_h5_data(self, filename, data):
        """Writes the data with shape [:, indices :]. Requires existing file."""
        if not os.path.exists(filename):
            raise Exception("only works on existing files.")

        indices = self.get_parameter_indices(filename)

        with h5py.File(filename, "r+") as h5:
            dat = h5["MODEL/data"]
            data_copy = dat[:, :, :].copy()
            # avoid writing the file many times. work on array in memory
            for i in range(len(indices)):
                data_copy[:, indices[i], :] = data[:, i, :]

            # writing only works in sorted order. This sort can only happen after
            # the above executed to preserve the ordering that data came in
            indices.sort()
            dat[:, indices, :] = data_copy[:, indices, :]

    def get_tensor_order(self, filename):
        """
        Get the tensor order from a Salvus file.
        :param filename: filename
        :type filename: str
        """
        with h5py.File(filename, "r") as h5:
            num_gll = h5["MODEL"]["coordinates"].shape[1]
            dimension = h5["MODEL"]["coordinates"].shape[2]
        return round(num_gll ** (1 / dimension) - 1)

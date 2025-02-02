# Copyright 2020 The OATomobile Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
"""Handles the hosted CARLA autopilot expert demonstrations dataset."""

import glob
import os
import sys
import zipfile
from typing import Any
from typing import Callable
from typing import Generator
from typing import Mapping
from typing import Optional
from typing import Sequence
from typing import Union
from typing import Tuple
import glob
import matplotlib.pyplot as plt
import numpy as np
import scipy
from scipy import interpolate
from scipy.sparse import csr_matrix
import tqdm
import wget
from absl import logging

from oatomobile.core.dataset import Dataset
from oatomobile.core.dataset import Episode
from oatomobile.simulators.carla import defaults

import yaml
import random
import time


def map_waypoints_to_grid(waypoints, grid_res = 0.5, grid_size = (40,40)):
    # x: forward y: right side z: upward
    waypoints = waypoints[:,[1,0]]
    f = interpolate.interp1d(waypoints[:,0], waypoints[:,1], "linear")
    f_inv = interpolate.interp1d(waypoints[:,1], waypoints[:,0], "linear")
    min_x, max_x = np.min(waypoints[:,0]), np.max(waypoints[:,0])
    min_y, max_y = np.min(waypoints[:,1]), np.max(waypoints[:,1])
    x = np.arange(min_x, max_x, grid_res)
    y = np.arange(min_y, max_y, grid_res)
    xnew = f_inv(y)
    ynew = f(x)
    # Horizontal filling
    x_idx = np.round(x / grid_res + 0.5*grid_size[0]).astype("int") 
    y_idx = (np.round(ynew / grid_res)).astype("int")
    x_idx = np.clip(x_idx, a_min=0, a_max=grid_size[0]-1)
    y_idx = np.clip(y_idx, a_min=0, a_max=grid_size[1]-1) 
    data = np.ones_like(x_idx)
    grid_hfill = csr_matrix((data, (y_idx, x_idx)), shape = grid_size)
    # Vertical filling
    x_idx = np.round(xnew / grid_res + 0.5*grid_size[0]).astype("int") 
    y_idx = (np.round(y / grid_res)).astype("int") 
    x_idx = np.clip(x_idx, a_min=0, a_max=grid_size[0]-1) 
    y_idx = np.clip(y_idx, a_min=0, a_max=grid_size[1]-1) 
    data = np.ones_like(x_idx)
    grid_vfill = csr_matrix((data, (y_idx, x_idx)), shape = grid_size) 
    # Combine
    grid = (grid_hfill+grid_vfill).toarray()
    grid = (grid > 0).astype("int")
    return grid  

class CARLADataset(Dataset):
  """The CARLA autopilot expert demonstrations dataset."""

  def __init__(
      self,
      id: str,
  ) -> None:
    """Constructs a CARLA dataset.

    Args:
      id: One of {"raw", "examples", "processed"}.
    """
    if id not in ("raw", "examples", "processed"):
      raise ValueError("Unrecognised CARLA dataset id {}".format(id))
    self.id = id
    super(CARLADataset, self).__init__()
    dirname = os.path.dirname(__file__)
    config_dir = os.path.join(dirname, 'dataset_configurations.yaml')
    self.read_dataset_configurations(config_dir)

  def _get_uuid(self) -> str:
    """Returns the universal unique identifier of the dataset."""
    return "CARLATown01Autopilot{}-v0".format(self.id)

  @property
  def info(self) -> Mapping[str, Any]:
    """The dataset description."""
    return dict(
        uuid=self.uuid,
        town="Town01",
        agent="carsuite_baselines.rulebased.Autopilot",
        noise=0.2,
    )

  @property
  def url(self) -> str:
    """The URL where the dataset is hosted."""
    return os.path.join(
        "https://www.cs.ox.ac.uk",
        "people",
        "angelos.filos",
        "data",
        "oatomobile",
        "{}.zip".format(self.id),
    )

  def download_and_prepare(self, output_dir: str) -> None:
    """Downloads and prepares the dataset from the host URL.

    Args:
      output_dir: The absolute path where the prepared dataset is stored.
    """
    # Creates the necessary output directory.
    os.makedirs(output_dir, exist_ok=True)

    # Temporary zip file to use.
    zfname = os.path.join(output_dir, "{}.zip".format(self.id))
    # Downloads dataset from Google Drive.
    logging.debug("Starts downloading '{}' dataset".format(self.id))
    wget.download(
        url=self.url,
        out=zfname,
    )
    # Unzips data.
    logging.debug("Unzips the data from {}".format(zfname))
    with zipfile.ZipFile(zfname) as zfile:
      zfile.extractall(output_dir)
    # Removes the zip file.
    logging.debug("Removes the compressed {}".format(zfname))
    os.remove(zfname)
  
  def read_dataset_configurations(self, config_dir: str) -> None:
    """Reads dataset_configuration.yaml into a dictionary
    
    Args:
      config_dir: The absolute path where the dataset configuration file is stored.
    """
    with open(config_dir) as file:
      self.config = yaml.safe_load(file)

  @staticmethod
  def load_datum(
      fname: str,
      modalities: Sequence[str],
      mode: bool,
      dataformat: str = "HWC",
  ) -> Mapping[str, np.ndarray]:
    """Loads a single datum from the dataset.

    Args:
      fname: The absolute path to the ".npz" datum.
      modalities: The keys of the attributes to fetch.
      mode: If True, it labels its datum with {FORWARD, STOP, LEFT, RIGHT}.
      dataformat: The format of the 3D data, one of `{HWC, CHW}`.

    Returns:
      The datum in a dictionary, `NumPy`-friendly format.
    """
    assert dataformat in ("HWC", "CHW")

    dtype = np.float32
    sample = dict()

    with np.load(fname) as datum:
      for attr in modalities:
        # Fetches the value.
        sample[attr] = datum[attr]
        # Converts scalars to 1D vectors.
        sample[attr] = np.atleast_1d(sample[attr])
        # Casts value to same type.
        sample[attr] = sample[attr].astype(dtype)
        if len(sample[attr].shape) == 3 and dataformat == "CHW":
          # Converts from HWC to CHW format.
          sample[attr] = np.transpose(sample[attr], (2, 0, 1))

    # Appends `mode` attribute where `{0: FORWARD, 1: STOP, 2: TURN}`.
    if mode and "player_future" in sample:
      plan = sample["player_future"]
      x_T, y_T = plan[-1, :2]
      # Norm of the vector (x_T, y_T).
      norm = np.linalg.norm([x_T, y_T])
      # Angle of vector (0, 0) -> (x_T, y_T).
      theta = np.degrees(np.arccos(x_T / (norm + 1e-3)))
      if norm < 3:  # STOP
        sample["mode"] = 1
      elif theta > 15:  # LEFT
        sample["mode"] = 2
      elif theta <= -15:  # RIGHT
        sample["mode"] = 3
      else:  # FORWARD
        sample["mode"] = 0
      sample["mode"] = np.atleast_1d(sample["mode"])
      sample["mode"] = sample["mode"].astype(dtype)

    # Records the path to the sample.
    sample["name"] = fname

    return sample
  
  def collect(self, 
              num_episodes: int, 
              output_dir: str,
              max_steps_per_episode: int = 1000,
              debug: bool = False):
    """Collect multiple episodes of data using configuration file

    Args: 
      num_episodes: number of episodes of game
      outout_dir: parent dirs to store data
      max_steps_per_episode: Caps environment's duration.
    """
    # Storage area.
    os.makedirs(output_dir, exist_ok=True)
    town = self.config["TOWN"]
    sensors = defaults.CARLA_SENSORS
    for weather_id in ["ClearNoon", "WetNoon", "HardRainNoon", "ClearSunset"]:
      os.makedirs(os.path.join(output_dir, weather_id), exist_ok=True)
    for i in range(num_episodes):
      print("Collecting Episode: %d..."%i)
      number_of_vehicles = random.randint(self.config["NumberOfVehicles"][0], 
                                          self.config["NumberOfVehicles"][1])
      number_of_pedestrians = random.randint(self.config["NumberOfPedestrians"][0], 
                                             self.config["NumberOfPedestrians"][1])

      weather = random.choice(self.config["WEATHERS"])
      origin, destination = random.choice(self.config["POSITIONS"])
      if random.random() < 0.5:
        origin, destination = destination, origin
      #origin, destination = 19, 66 #None, None
      print("Number of Vehicles: %d\nNumber of Pedestrians: %d"%(number_of_vehicles, number_of_pedestrians))
      print("Weather: %s"%weather)
      print("Origin index: %d\nDestination index: %d"%(origin,destination))
      data_dir = os.path.join(output_dir, weather)
      CARLADataset.collect_one_episode(town=town, 
                                       weather=weather, 
                                       output_dir=data_dir,
                                       num_vehicles=number_of_vehicles, 
                                       num_pedestrians=number_of_pedestrians, 
                                       num_steps=max_steps_per_episode, 
                                       spawn_point=origin,
                                       destination=destination,
                                       sensors=sensors, 
                                       render=False,
                                       debug=debug)
      # Wait 5s so the game can be closed completely
      time.sleep(15)

  @staticmethod
  def collect_one_episode(
      town: str,
      weather: str,
      output_dir: str,
      num_vehicles: int,
      num_pedestrians: int,
      num_steps: int = 1000,
      spawn_point: Optional[Union[int, "carla.Location"]] = None,  # pylint: disable=no-member
      destination: Optional[Union[int, "carla.Location"]] = None,  # pylint: disable=no-member

      sensors: Sequence[str] = (
          "acceleration",
          "velocity",
          "lidar",
          "is_at_traffic_light",
          "traffic_light_state",
          "actors_tracker",
      ),
      render: bool = False,
      debug: bool = False
  ) -> None:
    """Collects autopilot demonstrations for a single episode on CARLA.

    Args:
      town: The CARLA town id.
      output_dir: The full path to the output directory.
      num_vehicles: The number of other vehicles in the simulation.
      num_pedestrians: The number of pedestrians in the simulation.
      num_steps: The number of steps in the simulator.
      spawn_point: The hero vehicle spawn point. If an int is
        provided then the index of the spawn point is used.
        If None, then randomly selects a spawn point every time
        from the available spawn points of each map.
      destination: The final destination. If an int is
        provided then the index of the spawn point is used.
        If None, then randomly selects a spawn point every time
        from the available spawn points of each map.
      sensors: The list of recorded sensors.
      render: If True it spawn the `PyGame` display.
    """
    from oatomobile.baselines.rulebased.autopilot.agent import AutopilotAgent
    from oatomobile.core.loop import EnvironmentLoop
    from oatomobile.core.rl import FiniteHorizonWrapper
    from oatomobile.core.rl import SaveToDiskWrapper
    from oatomobile.core.rl import MonitorWrapper
    from oatomobile.envs.carla import CARLAEnv
    from oatomobile.envs.carla import TerminateOnCollisionWrapper

    # Initializes a CARLA environment.
    env = CARLAEnv(
        town=town,
        weather=weather,
        spawn_point=spawn_point,
        destination=destination,
        sensors=sensors,
        num_vehicles=num_vehicles,
        num_pedestrians=num_pedestrians
    ) 
    # Terminates episode if a collision occurs.
    env = TerminateOnCollisionWrapper(env)
    # Wraps the environment in an episode handler to store <observation, action> pairs.
    if debug:
      env = MonitorWrapper(env, output_fname="/data/datasets//data/datasets/carla/debug.gif")
    else:
      env = SaveToDiskWrapper(env=env, output_dir=output_dir)
    # Caps environment's duration.
    env = FiniteHorizonWrapper(env=env, max_episode_steps=num_steps)

    # Run a full episode.
    EnvironmentLoop(
        agent_fn=AutopilotAgent,
        environment=env,
        render_mode="human" if render else "none",
    ).run()

  @staticmethod
  def process(
      dataset_dir: str,
      output_dir: str,
      future_length: int = 80,
      past_length: int = 20,
      num_frame_skips: int = 5,
      grid_res: float = 0.5,
      grid_size: Tuple = (39,39)
  ) -> None:
    """Converts a raw dataset to demonstrations for imitation learning.

    Args:
      dataset_dir: The full path to the raw dataset.
      output_dir: The full path to the output directory.
      future_length: The length of the future trajectory.
      past_length: The length of the past trajectory.
      num_frame_skips: The number of frames to skip.
    """
    from oatomobile.utils import carla as cutil

    # Creates the necessary output directory.
    os.makedirs(output_dir, exist_ok=True)

    # Iterate over all episodes.
    for episode_token in tqdm.tqdm(filter(lambda x: len(x)==32, os.listdir(dataset_dir))):
      logging.debug("Processes {} episode".format(episode_token))
      # Initializes episode handler.
      episode = Episode(parent_dir=dataset_dir, token=episode_token)
      # Fetches all `.npz` files from the raw dataset.
      try:
        sequence = episode.fetch()
      except FileNotFoundError:
        print("File not found!")
        continue

      # Always keep `past_length+future_length+1` files open.
      if len(sequence) < past_length + future_length + 1:
        continue
      for i in tqdm.trange(
          past_length,
          len(sequence) - future_length,
          num_frame_skips,
      ):
        try:
          # Player context/observation.
          observation = episode.read_sample(sample_token=sequence[i])
          # Project waypoints to a topdown one-hot encoded image
          wpts_ohe = map_waypoints_to_grid(observation["goal"], 
                                           grid_res = grid_res, 
                                           grid_size = grid_size)
          current_location = observation["location"]
          current_rotation = observation["rotation"]

          # Build past trajectory.
          player_past = list()
          for j in range(past_length, 0, -1):
            past_location = episode.read_sample(
                sample_token=sequence[i - j],
                attr="location",
            )
            player_past.append(past_location)
          player_past = np.asarray(player_past)
          assert len(player_past.shape) == 2
          player_past = cutil.world2local(
              current_location=current_location,
              current_rotation=current_rotation,
              world_locations=player_past,
          )

          # Build future trajectory.
          player_future = list()
          player_future_control = list()
          player_future_speed = list()
          player_future_rotation = list()
          for j in range(1, future_length + 1):
            future_location = episode.read_sample(
                sample_token=sequence[i + j],
                attr="location")
            future_control = episode.read_sample(
                sample_token=sequence[i + j],
                attr="control")
            future_speed = episode.read_sample(
                sample_token=sequence[i + j],
                attr="velocity")
            future_rotation = episode.read_sample(
                sample_token=sequence[i + j],
                attr="rotation")
            player_future.append(future_location)
            player_future_control.append(future_control)
            player_future_speed.append(future_speed)
            # Relative to current rotation
            relative_rotation = future_rotation - current_rotation
            relative_yaw = relative_rotation[1]
            if relative_yaw < 0:
              relative_yaw += 360
            if relative_yaw > 360:
              relative_yaw -= 360
            # Cast to [-pi, pi]
            if relative_yaw > 180:
              relative_yaw = relative_yaw - 360
            relative_rotation[1] = relative_yaw
            player_future_rotation.append(relative_rotation)
          player_future = np.asarray(player_future)
          # No need to transform control actions to local frame
          player_future_control = np.asarray(player_future_control)
          # No need to transform speed since all in ego-frame
          player_future_speed = np.asarray(player_future_speed)
          player_future_rotation = np.asarray(player_future_rotation)
          player_future = np.asarray(player_future)
          # No need to transform control actions to local frame
          player_future_control = np.asarray(player_future_control)
          # No need to transform speed since all in ego-frame
          player_future_speed = np.asarray(player_future_speed)
          assert len(player_future.shape) == 2
          player_future = cutil.world2local(
              current_location=current_location,
              current_rotation=current_rotation,
              world_locations=player_future,
          )
          # Appends `mode` attribute where `{0: FORWARD, 1: STOP, 2: LEFT, 3: RIGHT}`.
          plan = player_future
          x_T, y_T = plan[-1, :2]
          # Norm of the vector (x_T, y_T).
          norm = np.linalg.norm([x_T, y_T])
          # Angle of vector (0, 0) -> (x_T, y_T).
          theta = np.degrees(np.arctan(y_T / (x_T + 1e-3)))
          if norm < 3:  # STOP
            mode = 1
          elif theta > 15:  # RIGHT
            mode = 2
          elif theta <= -15:  # LEFT
            mode = 3
          else:  # FORWARD
            mode = 0
          mode = np.atleast_1d(mode)
          # Store to ouput directory.
          np.savez_compressed(
              os.path.join(output_dir, "{}.npz".format(sequence[i])),
              **observation,
              player_future=player_future,
              player_past=player_past,
              player_future_control = player_future_control,
              player_future_speed = player_future_speed,
              player_future_rotation = player_future_rotation,
              waypoints_ohe = wpts_ohe,
              mode = mode
          )

        except Exception as e:
          if isinstance(e, KeyboardInterrupt):
            sys.exit(0)

  @staticmethod
  def plot_datum(
      fname: str,
      output_dir: str,
  ) -> None:
    """Visualizes a datum from the dataset.

    Args:
      fname: The absolute path to the datum.
      output_dir: The full path to the output directory.
    """
    from oatomobile.utils import graphics as gutil

    COLORS = [
        "#0071bc",
        "#d85218",
        "#ecb01f",
        "#7d2e8d",
        "#76ab2f",
        "#4cbded",
        "#a1132e",
    ]

    # Creates the necessary output directory.
    os.makedirs(output_dir, exist_ok=True)

    # Load datum.
    datum = np.load(fname)

    # Draws LIDAR.
    if "lidar" in datum:
      bev_meters = 25.0
      lidar = gutil.lidar_2darray_to_rgb(datum["lidar"])
      fig, ax = plt.subplots(figsize=(3.0, 3.0))
      ax.imshow(
          np.transpose(lidar, (1, 0, 2)),
          extent=(-bev_meters, bev_meters, bev_meters, -bev_meters),
      )
      ax.set(frame_on=False)
      ax.get_xaxis().set_visible(False)
      ax.get_yaxis().set_visible(False)
      fig.savefig(
          os.path.join(output_dir, "lidar.png"),
          bbox_inches="tight",
          pad_inches=0,
          transparent=True,
      )

    # Draws first person camera-view.
    if "front_camera_rgb" in datum:
      front_camera_rgb = datum["front_camera_rgb"]
      fig, ax = plt.subplots(figsize=(3.0, 3.0))
      ax.imshow(front_camera_rgb)
      ax.set(frame_on=False)
      ax.get_xaxis().set_visible(False)
      ax.get_yaxis().set_visible(False)
      fig.savefig(
          os.path.join(output_dir, "front_camera_rgb.png"),
          bbox_inches="tight",
          pad_inches=0,
          transparent=True,
      )

    # Draws bird-view camera.
    if "bird_view_camera_cityscapes" in datum:
      bev_meters = 25.0
      bird_view_camera_cityscapes = datum["bird_view_camera_cityscapes"]
      fig, ax = plt.subplots(figsize=(3.0, 3.0))
      ax.imshow(
          bird_view_camera_cityscapes,
          extent=(-bev_meters, bev_meters, bev_meters, -bev_meters),
      )
      # Draw past if available.
      if "player_past" in datum:
        player_past = datum["player_past"]
        ax.plot(
            player_past[..., 1],
            -player_past[..., 0],
            marker="x",
            markersize=4,
            color=COLORS[0],
            alpha=0.15,
        )
      # Draws future if available.
      if "player_future" in datum:
        player_future = datum["player_future"]
        ax.plot(
            player_future[..., 1],
            -player_future[..., 0],
            marker="o",
            markersize=4,
            color=COLORS[1],
            alpha=0.15,
        )
      # Draws goals if available.
      if "goal" in datum:
        goal = datum["goal"]
        ax.plot(
            goal[..., 1],
            -goal[..., 0],
            marker="D",
            markersize=6,
            color=COLORS[2],
            linestyle="None",
            alpha=0.25,
            label=r"$\mathcal{G}$",
        )
      ax.set(frame_on=False)
      ax.get_xaxis().set_visible(False)
      ax.get_yaxis().set_visible(False)
      fig.savefig(
          os.path.join(output_dir, "bird_view_camera_cityscapes.png"),
          bbox_inches="tight",
          pad_inches=0,
          transparent=True,
      )

    # Draws bird-view camera.
    if "bird_view_camera_rgb" in datum:
      bev_meters = 25.0
      bird_view_camera_rgb = datum["bird_view_camera_rgb"]
      fig, ax = plt.subplots(figsize=(3.0, 3.0))
      ax.imshow(
          bird_view_camera_rgb,
          extent=(-bev_meters, bev_meters, bev_meters, -bev_meters),
      )
      # Draw past if available.
      if "player_past" in datum:
        player_past = datum["player_past"]
        ax.plot(
            player_past[..., 1],
            -player_past[..., 0],
            marker="x",
            markersize=4,
            color=COLORS[0],
            alpha=0.15,
        )
      # Draws future if available.
      if "player_future" in datum:
        player_future = datum["player_future"]
        ax.plot(
            player_future[..., 1],
            -player_future[..., 0],
            marker="o",
            markersize=4,
            color=COLORS[1],
            alpha=0.15,
        )
      ax.set(frame_on=False)
      ax.get_xaxis().set_visible(False)
      ax.get_yaxis().set_visible(False)
      fig.savefig(
          os.path.join(output_dir, "bird_view_camera_rgb.png"),
          bbox_inches="tight",
          pad_inches=0,
          transparent=True,
      )

  @classmethod
  def plot_coverage(
      cls,
      dataset_dir: str,
      output_fname: str,
      color: int = 0,
  ) -> None:
    """Visualizes all the trajectories in the dataset.

    Args:
      dataset_dir: The parent directory of all the dataset.
      output_fname: The full path to the output filename.
      color: The index of the color to use for the trajectories.
    """
    COLORS = [
        "#0071bc",
        "#d85218",
        "#ecb01f",
        "#7d2e8d",
        "#76ab2f",
        "#4cbded",
        "#a1132e",
    ]

    # Fetches all the data points.
    data_files = glob.glob(
        os.path.join(dataset_dir, "**", "*.npz"),
        recursive=True,
    )

    # Container that stores all locaitons.
    locations = list()
    for npz_fname in tqdm.tqdm(data_files):
      try:
        locations.append(
            cls.load_datum(
                npz_fname,
                modalities=["location"],
                mode=False,
            )["location"])
      except Exception as e:
        if isinstance(e, KeyboardInterrupt):
          sys.exit(0)
    locations = np.asarray(locations)

    # Scatter plots all locaitons.
    fig, ax = plt.subplots(figsize=(3.0, 3.0))
    ax.scatter(
        locations[..., 0],
        locations[..., 1],
        s=5,
        alpha=0.01,
        color=COLORS[color % len(COLORS)],
    )
    ax.set(title=dataset_dir, frame_on=False)
    ax.get_xaxis().set_visible(False)
    ax.get_yaxis().set_visible(False)
    fig.savefig(
        os.path.join(output_fname),
        bbox_inches="tight",
        pad_inches=0,
        transparent=True,
    )

  @classmethod
  def as_tensorflow(
      cls,
      dataset_dir: str,
      modalities: Sequence[str],
      mode: bool = False,
  ) -> "tensorflow.data.Dataset":
    """Implements a data reader and loader for the expert demonstrations.

    Args:
      dataset_dir: The absolute path to the raw dataset.
      modalities: The keys of the attributes to fetch.
      mode: If True, it labels its datum with {FORWARD, STOP, LEFT, RIGHT}.

    Returns:
      The unbatched `TensorFlow` dataset.
    """
    import tensorflow as tf

    # Fetches all the filenames.
    filenames = glob.glob(os.path.join(dataset_dir, "*.npz"))

    # Gets shapes of output tensors.
    output_shapes = dict()
    with np.load(filenames[0]) as datum:
      for modality in modalities:
        output_shapes[modality] = tf.TensorShape(
            np.atleast_1d(datum[modality]).shape)

    # Appends "mode" attribute.
    if mode:
      output_shapes["mode"] = tf.TensorShape((1,))

    # Sets all output types to `tf.float32`.
    output_types = {modality: tf.float32 for modality in output_shapes.keys()}

    return tf.data.Dataset.from_generator(
        generator=lambda: (cls.load_datum(
            npz_fname,
            modalities,
            mode,
            dataformat="HWC",
        ) for npz_fname in filenames),
        output_types=output_types,
        output_shapes=output_shapes,
    )

  @classmethod
  def as_numpy(
      cls,
      dataset_dir: str,
      modalities: Sequence[str],
      mode: bool = False,
  ) -> Generator[Mapping[str, np.ndarray], None, None]:
    """Implements a data reader and loader for the expert demonstrations.

    Args:
      dataset_dir: The absolute path to the raw dataset.
      modalities: The keys of the attributes to fetch.
      mode: If True, it labels its datum with {FORWARD, STOP, LEFT, RIGHT}.

    Returns:
      The unbatched `NumPy` dataset.
    """
    import tensorflow_datasets as tfds

    return tfds.as_numpy(cls.as_tensorflow(dataset_dir, modalities, mode))

  @classmethod
  def as_torch(
      cls,
      dataset_dir: str,
      modalities: Sequence[str],
      transform: Optional[Callable[[Any], Any]] = None,
      mode: bool = False,
      only_array: bool = False,
  ) -> "torch.utils.data.Dataset":
    """Implements a data reader and loader for the expert demonstrations.

    Args:
      dataset_dir: The absolute path to the raw dataset.
      modalities: The keys of the attributes to fetch.
      transform: The transformations applied on each datum.
      mode: If True, it labels its datum with {FORWARD, STOP, LEFT, RIGHT}.
      only_array: If True, it removes all the keys that are non-array, useful
        when training a model and want to run `.to(device)` without errors.

    Returns:
      The unbatched `PyTorch` dataset.
    """
    import torch

    class PyTorchDataset(torch.utils.data.Dataset):
      """Implementa a data reader for the expert demonstrations."""

      def __init__(
          self,
          dataset_dir: str,
          modalities: Sequence[str],
          transform: Optional[Callable[[Any], Any]] = None,
          mode: bool = False,
      ) -> None:
        """A simple `PyTorch` dataset.

        Args:
          dataset_dir: The absolute path to the raw dataset.
          modalities: The keys of the attributes to fetch.
          mode: If True, it labels its datum with {FORWARD, STOP, LEFT, RIGHT}.
        """
        # Internalise hyperparameters.
        self._modalities = modalities
        self._npz_files = glob.glob(os.path.join(dataset_dir, "*.npz"))
        self._transform = transform
        self._mode = mode

      def __len__(self) -> int:
        """Returns the size of the dataset."""
        return len(self._npz_files)

      def __getitem__(
          self,
          idx: int,
      ) -> Mapping[str, np.ndarray]:
        """Loads a single datum.

        Returns:
          The datum in `NumPy`-friendly format.
        """
        # Loads datum from dataset.
        sample = cls.load_datum(
            fname=self._npz_files[idx],
            modalities=self._modalities,
            mode=self._mode,
            dataformat="CHW",
        )

        # Filters out non-array keys.
        for key in list(sample):
          if not isinstance(sample[key], np.ndarray):
            sample.pop(key)

        # Applies (optional) transformation to all values.
        if self._transform is not None:
          sample = {key: self._transform(val) for (key, val) in sample.items()}
        return sample

    return PyTorchDataset(dataset_dir, modalities, transform, mode)

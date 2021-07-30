import oatomobile
import oatomobile.envs
from oatomobile.datasets.carla import CARLADataset

if __name__ == "__main__":
    dataset = CARLADataset(id = "raw")
    dataset.collect(num_episodes = 1,
                    output_dir = "/data/datasets/carla/data_oatomobile_collected/raw",
                    max_steps_per_episode = 1000,
                    debug = False)

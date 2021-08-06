from oatomobile.datasets.carla import CARLADataset
import os

if __name__ == "__main__":
    data = CARLADataset("raw")
    # ["ClearNoon", "WetNoon", "HardRainNoon", "ClearSunset"]
    weather = 'ClearSunset'
    print(weather)
    # Parent Directories
    parent_dir = '/data/datasets/carla/data_oatomobile_collected/'
    output_dir = os.path.join(parent_dir, 'processed', weather)
    dataset_dir = os.path.join(parent_dir, 'raw', weather)
    data.process(dataset_dir, output_dir)
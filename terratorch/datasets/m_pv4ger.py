import ast
import json
import pickle
from collections.abc import Sequence
from pathlib import Path

import albumentations as A
import h5py
import matplotlib.pyplot as plt
import numpy as np
import torch
from albumentations.pytorch import ToTensorV2
from torchgeo.datasets import NonGeoDataset

from terratorch.datasets.utils import (
    clip_image,
    default_transform,
    validate_bands,
)


class MPv4gerNonGeo(NonGeoDataset):
    """NonGeo dataset implementation for [M-PV4GER](https://github.com/ServiceNow/geo-bench?tab=readme-ov-file)."""
    all_band_names = ("BLUE", "GREEN", "RED")

    rgb_bands = ("RED", "GREEN", "BLUE")

    BAND_SETS = {"all": all_band_names, "rgb": rgb_bands}

    splits = {"train": "train", "val": "valid", "test": "test"}

    data_dir = "m-pv4ger"
    partition_file_template = "{partition}_partition.json"

    def __init__(
        self,
        data_root: str,
        split: str = "train",
        bands: Sequence[str] = BAND_SETS["all"],
        transform: A.Compose | None = None,
        partition: str = "default",
        use_metadata: bool = False,
    ) -> None:
        """Initialize the dataset.

        Args:
            data_root (str): Path to the data root directory.
            split (str): One of 'train', 'val', or 'test'.
            bands (Sequence[str]): Bands to be used. Defaults to all bands.
            transform (A.Compose | None): Albumentations transform to be applied.
                Defaults to None, which applies default_transform().
            partition (str): Partition name for the dataset splits. Defaults to 'default'.
            use_metadata (bool): Whether to return metadata info (location coordinates).
        """
        super().__init__()

        if split not in self.splits:
            msg = f"Incorrect split '{split}', please choose one of {list(self.splits.keys())}."
            raise ValueError(msg)
        split_name = self.splits[split]
        self.split = split

        validate_bands(bands, self.all_band_names)
        self.bands = bands
        self.band_indices = np.array([self.all_band_names.index(b) for b in bands])

        self.use_metadata = use_metadata

        self.data_root = Path(data_root)
        self.data_directory = self.data_root / self.data_dir

        partition_file = self.data_directory / self.partition_file_template.format(partition=partition)
        with open(partition_file) as file:
            partitions = json.load(file)

        if split_name not in partitions:
            msg = f"Split '{split_name}' not found."
            raise ValueError(msg)

        self.image_files = [self.data_directory / f"{filename}.hdf5" for filename in partitions[split_name]]

        self.transform = transform if transform else default_transform

    def __len__(self) -> int:
        return len(self.image_files)

    def __getitem__(self, index: int) -> dict[str, torch.Tensor]:
        file_path = self.image_files[index]
        image_id = file_path.stem

        with h5py.File(file_path, "r") as h5file:
            keys = sorted(h5file.keys())
            keys = np.array([key for key in keys if key != "label"])[self.band_indices]
            bands = [np.array(h5file[key]) for key in keys]

            image = np.stack(bands, axis=-1)
            attr_dict = pickle.loads(ast.literal_eval(h5file.attrs["pickle"]))  # noqa: S301
            class_index = attr_dict["label"]

        output = {"image": image.astype(np.float32)}

        if self.transform:
            output = self.transform(**output)

        output["label"] = class_index

        if self.use_metadata:
            output["location_coords"] = self._get_coords(image_id)

        return output

    def _get_coords(self, image_id: str) -> torch.Tensor:
        """Extract spatial coordinates from the image ID."""
        lat_str, lon_str = image_id.split(",")
        latitude = float(lat_str)
        longitude = float(lon_str)
        return torch.tensor([latitude, longitude], dtype=torch.float32)

    def plot(self, sample: dict[str, torch.Tensor], suptitle: str | None = None) -> plt.Figure:
        """Plot a sample from the dataset.

        Args:
            sample (dict[str, torch.Tensor]): A sample returned by :meth:`__getitem__`.
            suptitle (str | None): Optional string to use as a suptitle.

        Returns:
            matplotlib.figure.Figure: A matplotlib Figure with the rendered sample.
        """
        rgb_indices = [self.bands.index(band) for band in self.rgb_bands if band in self.bands]
        if len(rgb_indices) != 3:
            msg = "Dataset doesn't contain some of the RGB bands"
            raise ValueError(msg)

        image = sample["image"]
        label = sample["label"]

        if torch.is_tensor(image):
            image = image.permute(1, 2, 0).numpy()

        rgb_image = image[:, :, rgb_indices]
        rgb_image = clip_image(rgb_image)

        fig, ax = plt.subplots(figsize=(6, 6))
        ax.imshow(rgb_image)
        ax.axis("off")
        ax.set_title(f"Class: {label}")

        if suptitle:
            plt.suptitle(suptitle)

        return fig

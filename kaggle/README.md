# Kaggle mirrors

`.github/workflows/kaggle-publish.yml` copies each GitHub release to Kaggle so internet-off
notebooks get the same build:

- `wheels-dataset/`: metadata for the dataset `manwithacat/kickmix-fork-wheels`. Each release
  pushes a new dataset version holding the Linux x86-64 wheels (Kaggle's CPU image) and a
  `SHA256SUMS` listing exactly those wheels, taken unchanged from the release.
- `kickmix-tools/`: metadata for the utility script `manwithacat/kickmix-tools`. Each release
  pushes `tools/kickmix_tools.py` from the tagged commit as a new script version.

GitHub releases are the source of truth; Kaggle only mirrors them. Authentication uses the
repository secret `KAGGLE_API_TOKEN` (a Kaggle API token). The dataset's About text, tags and
other metadata are maintained on Kaggle, not here: a version push changes files only.

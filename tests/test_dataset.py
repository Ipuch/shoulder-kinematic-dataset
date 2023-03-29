import numpy as np
import pandas as pd
import pytest


def test_no_nan_in_columns():
    file = "../data/dataset_clean.csv"
    df = pd.read_csv(file)
    # define a list of columns that should contain no nan
    col = [
        "dataset_authors",
        "article_title",
        "article_year",
        "article_author_year",
        "article_journal",
        "in_vivo",
        "experimental_mean",
        "type_of_movement",
        "humeral_motion",
        "joint",
        "parent",
        "child",
    ]
    # verify that all the columns in the list contain no nan
    for c in col:
        print(c)
        np.testing.assert_array_equal(df[c].isna().values, np.zeros(len(df[c].values)))
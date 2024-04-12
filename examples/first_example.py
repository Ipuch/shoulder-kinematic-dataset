import spartacus as sp


def main():
    spartacus_dataset = sp.load()
    print(spartacus_dataset.confident_data_values)
    spartacus_dataset.export()
    return spartacus_dataset.confident_data_values


if __name__ == "__main__":
    main()

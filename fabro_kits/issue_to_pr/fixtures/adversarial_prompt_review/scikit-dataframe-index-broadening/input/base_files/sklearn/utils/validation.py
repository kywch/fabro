def check_array(array, *, accept_dataframe=False):
    if hasattr(array, "to_numpy"):
        if not accept_dataframe:
            raise TypeError("DataFrame input is not accepted here")
        return array.to_numpy()
    return array

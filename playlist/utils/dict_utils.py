def find_deepest_metadata_key(data, search_key):
    """
    Recursively searches for the 'text' value corresponding to a given 'title' key
    in a deeply nested structure of lists and dictionaries.
    """
    if isinstance(data, dict):
        if data.get("title") == search_key and "text" in data:
            return data["text"]
        for value in data.values():
            result = find_deepest_metadata_key(value, search_key)
            if result is not None:
                return result
    elif isinstance(data, list):
        for item in data:
            result = find_deepest_metadata_key(item, search_key)
            if result is not None:
                return result
    return None
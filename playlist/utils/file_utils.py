import hashlib

from unidecode import unidecode

def calculate_md5(file_path: str) -> str:
    """
    Calculates the MD5 hash of a file.
    Reads the file in chunks to handle large files efficiently.
    """
    hash_md5 = hashlib.md5()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except FileNotFoundError:
        return "" # Return empty string if file does not exist
    

def sanitize_string(filename: str):
    """
    Sanitize a string to be used in a filename. Removes invalid characters,
    adjusts casing, and transliterates to Latin characters.
    """
    # Attempt to transliterate to ASCII, removing accents etc.
    filename = unidecode(filename)

    # Manually remove content within parentheses, as it's often extra info
    new_filename = ""
    in_parentheses = 0
    for char in filename:
        if char == '(':
            in_parentheses += 1
        elif char == ')' and in_parentheses > 0:
            in_parentheses -= 1
        elif in_parentheses == 0:
            new_filename += char
    filename = new_filename.strip()

    # Replace invalid characters
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        filename = filename.replace(char, '')
    filename = filename.replace('&', 'and') # Replace '&' for better compatibility

    # Capitalize each word for a clean look
    filename = ' '.join(word.capitalize() for word in filename.split())

    if not filename:
        return "Unknown" # Return a default if sanitization results in an empty string

    return filename
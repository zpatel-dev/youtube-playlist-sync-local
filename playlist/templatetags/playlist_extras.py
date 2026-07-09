import os

from django import template

register = template.Library()

@register.filter(name='get_item')
def get_item(dictionary, key):
    """
    Allows accessing a dictionary item by a variable key in Django templates.
    Usage: {{ my_dictionary|get_item:my_key }}
    """
    return dictionary.get(key)


@register.filter(name='duration')
def duration(seconds):
    """Format a number of seconds as m:ss (or h:mm:ss). Blank if unknown."""
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return ''
    if total < 0:
        return ''
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f'{hours}:{minutes:02d}:{secs:02d}'
    return f'{minutes}:{secs:02d}'


@register.filter(name='basename')
def basename(path):
    """Return just the filename from a full path (e.g. the tagged mp3 name)."""
    return os.path.basename(str(path)) if path else ''
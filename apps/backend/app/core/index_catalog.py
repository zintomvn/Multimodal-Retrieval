"""The read alias is independent of immutable physical import versions."""
import os


def annotation_index():
    return os.getenv('ANNOTATION_READ_INDEX', 'keyframe_annotations')

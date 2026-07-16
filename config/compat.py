import sys

import django


def patch_django_context_copy_for_python_314():
    """
    Django 5.1's BaseContext.__copy__ uses copy(super()), which breaks on
    Python 3.14 when admin copies template contexts. Django versions with
    native Python 3.14 support don't need this compatibility patch.
    """
    if sys.version_info < (3, 14) or django.VERSION >= (5, 2):
        return

    from django.template.context import BaseContext

    if getattr(BaseContext, "_pos_saas_python314_copy_patch", False):
        return

    def __copy__(self):
        duplicate = self.__class__.__new__(self.__class__)
        duplicate.__dict__.update(self.__dict__)
        duplicate.dicts = self.dicts[:]
        return duplicate

    BaseContext.__copy__ = __copy__
    BaseContext._pos_saas_python314_copy_patch = True

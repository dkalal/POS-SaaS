from contextvars import ContextVar


_current_tenant_id = ContextVar("current_tenant_id", default=None)


def set_current_tenant_id(tenant_id):
    return _current_tenant_id.set(tenant_id)


def reset_current_tenant_id(token):
    _current_tenant_id.reset(token)


def get_current_tenant_id():
    return _current_tenant_id.get()


from contextlib import contextmanager

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.test.signals import template_rendered

from tenants.forms import TenantBootstrapForm
from tenants.models import Tenant
from tenants.services import bootstrap_first_tenant


@contextmanager
def _suppress_template_render_signal():
    receivers = list(template_rendered.receivers)
    cache = template_rendered.sender_receivers_cache.copy()
    template_rendered.receivers = []
    template_rendered.sender_receivers_cache.clear()
    try:
        yield
    finally:
        template_rendered.receivers = receivers
        template_rendered.sender_receivers_cache = cache


def _error_list_markup(errors):
    if not errors:
        return ""
    return "".join(f'<div class="field-error">{escape(str(error))}</div>' for error in errors)


def _render_bootstrap_page(form, *, raw_key=None, tenant=None, api_key=None):
    if raw_key is not None and tenant is not None and api_key is not None:
        body = f"""
        <div class="hero success">
          <div class="badge">Bootstrap complete</div>
          <h1>Your first tenant is ready.</h1>
          <p>The workspace, owner membership, and first API key were created in one atomic transaction.</p>
        </div>
        <div class="panel">
          <h2>Copy the API key now</h2>
          <p class="muted">This secret is shown only once. Store it somewhere safe before continuing.</p>
          <div class="secret">{raw_key}</div>
          <div class="meta-grid">
            <div><span class="label">Tenant</span><strong>{tenant.name}</strong></div>
            <div><span class="label">Slug</span><strong>{tenant.slug}</strong></div>
            <div><span class="label">API key</span><strong>{api_key.label}</strong></div>
            <div><span class="label">Cost access</span><strong>{"Enabled" if api_key.can_view_cost else "Disabled"}</strong></div>
          </div>
          <div class="actions">
            <a class="primary" href="/">Go to dashboard</a>
            <a class="secondary" href="/admin/api/apikey/">Manage keys in admin</a>
          </div>
        </div>
        """
    else:
        body = f"""
        <div class="hero">
          <div class="badge">First-run setup</div>
          <h1>Bootstrap the first tenant.</h1>
          <p>Create the initial workspace, seat yourself as owner/admin, and mint the first API key in one secure step.</p>
        </div>
        <div class="panel">
          <form method="post" class="form">
            {_error_list_markup(form.non_field_errors())}
            <label>
              <span>Tenant name</span>
              {form.tenant_name}
              <small>{form.tenant_name.help_text}</small>
              {_error_list_markup(form.tenant_name.errors)}
            </label>
            <label>
              <span>Tenant slug</span>
              {form.tenant_slug}
              <small>{form.tenant_slug.help_text}</small>
              {_error_list_markup(form.tenant_slug.errors)}
            </label>
            <label>
              <span>Initial API key label</span>
              {form.api_key_label}
              <small>{form.api_key_label.help_text}</small>
              {_error_list_markup(form.api_key_label.errors)}
            </label>
            <label class="checkbox">
              {form.api_key_can_view_cost}
              <div>
                <span>Allow cost price access</span>
                <small>{form.api_key_can_view_cost.help_text}</small>
              </div>
            </label>
            {_error_list_markup(form.api_key_can_view_cost.errors)}
            <button type="submit">Create tenant and key</button>
          </form>
        </div>
        """

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Bootstrap | POS SaaS</title>
  <style>
    :root {{
      --bg: #081120;
      --panel: rgba(10, 18, 35, 0.82);
      --text: #e5eefb;
      --muted: #9fb0c8;
      --accent: #34d399;
      --accent-2: #38bdf8;
      --border: rgba(148, 163, 184, 0.2);
      --danger: #fca5a5;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 24px;
      color: var(--text);
      background:
        radial-gradient(circle at top left, rgba(56, 189, 248, 0.22), transparent 30%),
        radial-gradient(circle at 80% 20%, rgba(52, 211, 153, 0.18), transparent 25%),
        linear-gradient(180deg, #020617 0%, #081120 100%);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
    }}
    a {{ color: inherit; text-decoration: none; }}
    .shell {{ width: min(1040px, 100%); }}
    .hero, .panel {{
      border: 1px solid var(--border);
      background: var(--panel);
      backdrop-filter: blur(18px);
      box-shadow: 0 22px 56px rgba(2, 6, 23, 0.28);
      border-radius: 28px;
    }}
    .hero {{
      padding: 30px;
      margin-bottom: 18px;
      background:
        linear-gradient(135deg, rgba(8, 17, 32, 0.96), rgba(8, 17, 32, 0.78)),
        radial-gradient(circle at top right, rgba(52, 211, 153, 0.16), transparent 28%);
    }}
    .hero.success {{
      background:
        linear-gradient(135deg, rgba(8, 17, 32, 0.96), rgba(8, 17, 32, 0.78)),
        radial-gradient(circle at top right, rgba(56, 189, 248, 0.16), transparent 28%);
    }}
    .hero h1 {{ margin: 16px 0 10px; font-size: clamp(2rem, 5vw, 3.75rem); line-height: 1.02; }}
    .hero p {{ margin: 0; max-width: 70ch; color: var(--muted); }}
    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 6px 12px;
      border-radius: 999px;
      background: rgba(56, 189, 248, 0.14);
      color: #c7eaff;
      font-size: 0.84rem;
      letter-spacing: 0.02em;
    }}
    .panel {{
      padding: 26px;
    }}
    .form {{
      display: grid;
      gap: 18px;
    }}
    .field-error {{
      color: var(--danger);
      font-size: 0.9rem;
      line-height: 1.4;
    }}
    label {{
      display: grid;
      gap: 8px;
    }}
    label span {{
      font-weight: 650;
    }}
    label small, .muted {{
      color: var(--muted);
    }}
    input[type="text"], input[type="checkbox"] {{
      accent-color: var(--accent);
    }}
    .setup-input {{
      width: 100%;
      padding: 13px 14px;
      border-radius: 14px;
      border: 1px solid var(--border);
      background: rgba(15, 23, 42, 0.92);
      color: var(--text);
    }}
    .checkbox {{
      grid-template-columns: auto 1fr;
      align-items: start;
      gap: 12px;
      padding: 14px 16px;
      border: 1px solid var(--border);
      border-radius: 18px;
      background: rgba(15, 23, 42, 0.6);
    }}
    .checkbox input {{ margin-top: 3px; }}
    .actions {{
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      margin-top: 6px;
    }}
    button, .primary, .secondary {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 46px;
      padding: 0 16px;
      border-radius: 14px;
      font-weight: 700;
    }}
    button, .primary {{
      border: 0;
      background: linear-gradient(135deg, var(--accent-2), var(--accent));
      color: #06121f;
    }}
    .secondary {{
      border: 1px solid var(--border);
      background: rgba(15, 23, 42, 0.7);
    }}
    .secret {{
      margin: 18px 0;
      padding: 16px;
      border-radius: 18px;
      border: 1px dashed rgba(148, 163, 184, 0.32);
      background: rgba(2, 6, 23, 0.58);
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      word-break: break-all;
    }}
    .meta-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }}
    .meta-grid > div {{
      padding: 14px;
      border-radius: 18px;
      border: 1px solid var(--border);
      background: rgba(15, 23, 42, 0.55);
    }}
    .label {{
      display: block;
      font-size: 0.82rem;
      color: var(--muted);
      margin-bottom: 4px;
    }}
    @media (max-width: 720px) {{
      body {{ padding: 14px; }}
      .hero, .panel {{ border-radius: 22px; }}
      .meta-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    {body}
  </main>
</body>
</html>"""


@login_required
def bootstrap(request):
    if Tenant.objects.filter(is_active=True).exists():
        return redirect("dashboard")

    if request.method == "POST":
        form = TenantBootstrapForm(request.POST)
        if form.is_valid():
            tenant, _, api_key, raw_key = bootstrap_first_tenant(
                owner=request.user,
                tenant_name=form.cleaned_data["tenant_name"],
                tenant_slug=form.cleaned_data.get("tenant_slug", ""),
                api_key_label=form.cleaned_data["api_key_label"],
                api_key_can_view_cost=form.cleaned_data["api_key_can_view_cost"],
            )
            request.session["current_tenant_id"] = tenant.pk
            with _suppress_template_render_signal():
                html = render_to_string(
                    "tenants/bootstrap.html",
                    {
                        "form": form,
                        "created": True,
                        "tenant": tenant,
                        "api_key": api_key,
                        "raw_key": raw_key,
                    },
                    request=request,
                )
            return HttpResponse(html)
    else:
        form = TenantBootstrapForm()

    with _suppress_template_render_signal():
        html = render_to_string("tenants/bootstrap.html", {"form": form, "created": False}, request=request)
    return HttpResponse(html)

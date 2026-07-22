from django.core.management.base import BaseCommand, CommandError

from audit.services import verify_audit_chain
from platform_admin.services import verify_platform_audit_chain
from tenants.models import Tenant


class Command(BaseCommand):
    help = "Verify HMAC chain integrity for one tenant or every tenant."

    def add_arguments(self, parser):
        parser.add_argument("--tenant-id", type=int)

    def handle(self, *args, **options):
        tenants = Tenant._base_manager.all().order_by("pk")
        if options["tenant_id"]:
            tenants = tenants.filter(pk=options["tenant_id"])
        found = False
        failures = []
        for tenant in tenants.iterator():
            found = True
            result = verify_audit_chain(tenant=tenant)
            if result["valid"]:
                self.stdout.write(
                    f"tenant={tenant.pk} valid checked={result['checked']} legacy={result['legacy']}"
                )
            else:
                failures.append((tenant.pk, result["failed_event_id"]))
                self.stderr.write(f"tenant={tenant.pk} INVALID event={result['failed_event_id']}")
        if options["tenant_id"] and not found:
            raise CommandError("Tenant not found.")
        if failures:
            raise CommandError(f"Audit verification failed for {len(failures)} tenant(s).")
        platform_result = verify_platform_audit_chain()
        if not platform_result["valid"]:
            raise CommandError(
                f"Platform audit verification failed at event {platform_result['failed_event_id']}."
            )
        self.stdout.write(
            f"platform valid checked={platform_result['checked']} legacy={platform_result['legacy']}"
        )
        self.stdout.write(self.style.SUCCESS("Audit verification completed successfully."))

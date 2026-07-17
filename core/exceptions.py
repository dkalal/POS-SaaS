from django.core.exceptions import PermissionDenied


class DomainError(Exception):
    """Base class for domain-level service failures."""


class InsufficientStockError(DomainError):
    pass


class PurchaseAlreadyReceivedError(DomainError):
    pass


class PurchaseNotReceivedError(DomainError):
    pass


class PurchaseNotDraftError(DomainError):
    pass


class InvalidPurchaseInputError(DomainError):
    pass


class StockAdjustmentNotDraftError(DomainError):
    pass


class StockAdjustmentAlreadyPostedError(DomainError):
    pass


class StockAdjustmentNotPostedError(DomainError):
    pass


class SaleAlreadyCancelledError(DomainError):
    pass


class SaleNotCompletedError(DomainError):
    pass


class PaymentMethodNotAllowedError(DomainError):
    pass


class PermissionDeniedError(PermissionDenied, DomainError):
    pass

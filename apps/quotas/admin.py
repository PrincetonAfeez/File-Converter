# "Django admin for quota models."
from django.contrib import admin

from .models import QuotaDecision, UsageLedger, UsageQuota

admin.site.register(UsageQuota)
admin.site.register(QuotaDecision)
admin.site.register(UsageLedger)

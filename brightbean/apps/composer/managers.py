from apps.common.managers import WorkspaceScopedManager


class ActivePostManager(WorkspaceScopedManager):
    def get_queryset(self):
        return super().get_queryset().filter(trashed_at__isnull=True)


class ActivePlatformPostManager(WorkspaceScopedManager):
    def get_queryset(self):
        # Calendar, publisher, REST and MCP also query copies directly.
        return super().get_queryset().filter(post__trashed_at__isnull=True)

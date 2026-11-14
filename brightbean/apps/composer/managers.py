from apps.common.managers import WorkspaceScopedManager


class ActivePostManager(WorkspaceScopedManager):
    def get_queryset(self):
        return super().get_queryset().filter(trashed_at__isnull=True)


class ActivePlatformPostManager(WorkspaceScopedManager):
    """Copies of content that is not in the trash.

    The filter is a subquery, not a join. A join would make
    ``select_for_update()`` without ``of=`` lock the parent ``Post`` row too,
    in copy -> content order, the opposite of the publisher and ``move_content``.
    """

    def get_queryset(self):
        from apps.composer.models import Post

        return super().get_queryset().filter(post_id__in=Post.all_objects.filter(trashed_at__isnull=True).values("pk"))

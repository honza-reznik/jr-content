"""Test helper: run the worker's email tasks now, in the test process."""

from background_task.models import Task

EMAIL_TASKS = (
    "apps.common.tasks.deliver_outgoing_email",
    "apps.notifications.tasks.dispatch_notification_delivery",
)


def run_email_queue(max_rounds: int = 10) -> int:
    """Run every queued email task that is due, as the worker would, and return how many ran.

    Tasks scheduled for later (a retry's backoff) are not run. Rounds repeat because a
    failed attempt queues its retry.
    """
    from background_task.tasks import bg_runner, tasks
    from django.utils import timezone

    import apps.common.tasks  # noqa: F401  (registers the task functions)
    import apps.notifications.tasks  # noqa: F401

    ran = 0
    for _ in range(max_rounds):
        due = list(Task.objects.filter(task_name__in=EMAIL_TASKS, run_at__lte=timezone.now()).order_by("run_at", "pk"))
        if not due:
            break
        for task in due:
            bg_runner(tasks._tasks[task.task_name], task)
            ran += 1
    return ran

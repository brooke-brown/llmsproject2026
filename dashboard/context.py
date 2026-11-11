from core.models import Enrollment

STAFF_ROLES = (Enrollment.Role.INSTRUCTOR, Enrollment.Role.TA)


def navigation(request):
    """What the header offers: Courses to staff, My projects to students, both to someone who
    is each, and nothing to someone who must change their password first."""
    user = request.user
    if not user.is_authenticated or user.must_change_password:
        return {"nav_staff": False, "nav_student": False, "nav_restricted": user.is_authenticated}
    roles = set(Enrollment.objects.filter(user=user).values_list("role", flat=True))
    return {
        "nav_staff": bool(roles & set(STAFF_ROLES)),
        "nav_student": Enrollment.Role.STUDENT in roles,
        "nav_restricted": False,
    }

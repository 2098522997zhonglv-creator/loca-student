"""
API 自动化测试模块的通用权限基类。

所有 api_* 应用的权限类都继承自 IsProjectMemberForResource，
遵循 WHartTest 三层权限模式：IsAuthenticated + HasModelPermission + IsProjectMember。
"""

from rest_framework import permissions
from projects.models import ProjectMember


class IsProjectMemberForResource(permissions.BasePermission):
    """
    通用项目成员权限检查基类。

    检查当前用户是否为 URL 中 project_pk 对应项目的成员。
    超级管理员(is_superuser=True)可以访问所有项目。

    子类可覆盖 get_project_from_object(obj) 来自定义从对象提取项目的方式，
    默认通过 obj.project 获取。
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        if request.user.is_superuser:
            return True

        project_pk = view.kwargs.get("project_pk")
        if not project_pk or str(project_pk).lower() in ("none", "null", "undefined"):
            return False

        try:
            project_pk = int(project_pk)
        except (ValueError, TypeError):
            return False

        return ProjectMember.objects.filter(
            project_id=project_pk,
            user=request.user,
            role__in=["owner", "admin", "member"],
        ).exists()

    def has_object_permission(self, request, view, obj):
        if not request.user or not request.user.is_authenticated:
            return False

        if request.user.is_superuser:
            return True

        project = self.get_project_from_object(obj)
        if project is None:
            return False

        return ProjectMember.objects.filter(
            project=project,
            user=request.user,
            role__in=["owner", "admin", "member"],
        ).exists()

    def get_project_from_object(self, obj):
        """从对象中提取关联的项目。子类可覆盖此方法。"""
        return getattr(obj, "project", None)


class IsProjectAdminForResource(permissions.BasePermission):
    """
    项目管理员权限检查。仅允许项目 owner 和 admin 角色。
    用于敏感操作如 exec() 自定义函数等。
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        if request.user.is_superuser:
            return True

        project_pk = view.kwargs.get("project_pk")
        if not project_pk or str(project_pk).lower() in ("none", "null", "undefined"):
            return False

        try:
            project_pk = int(project_pk)
        except (ValueError, TypeError):
            return False

        return ProjectMember.objects.filter(
            project_id=project_pk,
            user=request.user,
            role__in=["owner", "admin"],
        ).exists()

    def has_object_permission(self, request, view, obj):
        if not request.user or not request.user.is_authenticated:
            return False

        if request.user.is_superuser:
            return True

        project = getattr(obj, "project", None)
        if project is None:
            return False

        return ProjectMember.objects.filter(
            project=project,
            user=request.user,
            role__in=["owner", "admin"],
        ).exists()

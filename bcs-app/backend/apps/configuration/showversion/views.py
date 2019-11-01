# -*- coding: utf-8 -*-
#
# Tencent is pleased to support the open source community by making 蓝鲸智云PaaS平台社区版 (BlueKing PaaS Community Edition) available.
# Copyright (C) 2017-2019 THL A29 Limited, a Tencent company. All rights reserved.
# Licensed under the MIT License (the "License"); you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://opensource.org/licenses/MIT
#
# Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on
# an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.
#
import json

from django.utils import timezone
from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework.renderers import BrowsableAPIRenderer
from rest_framework.exceptions import ValidationError

from backend.activity_log import client
from backend.apps.configuration import models
from backend.apps.configuration.mixins import TemplatePermission
from backend.apps.instance.utils import has_instance_of_show_version
from backend.utils.renderers import BKAPIRenderer
from .serializers import ShowVersionCreateSLZ, ShowVersionWithEntitySLZ, GetShowVersionSLZ, ResourceConfigSLZ, \
    ListShowVersionSLZ, ListShowVersionISLZ


def get_draft_show_version(template):
    return {
        'show_version_id': -1,
        'real_version_id': -1,
        'name': "草稿",
        'updator': template.draft_updator,
        'updated': timezone.localtime(template.updated).strftime('%Y-%m-%d %H:%M:%S')
    }


class ShowVersionViewSet(viewsets.ViewSet, TemplatePermission):
    renderer_classes = (BKAPIRenderer, BrowsableAPIRenderer)

    def _create_or_update_with_ventity(self, create_data):
        show_version_name = create_data['name']
        username = create_data['username']
        template = create_data['template']
        show_version_id = create_data['show_version_id']
        real_version_id = create_data['real_version_id']

        if show_version_id == 0:
            show_version, _ = models.ShowVersion.default_objects.update_or_create(
                name=show_version_name, template_id=template.id,
                defaults={
                    'is_deleted': False, 'deleted_time': None,
                    'updator': username, 'creator': username,
                    'real_version_id': real_version_id,
                    "history": [real_version_id, ]
                }
            )
        else:
            show_version = models.ShowVersion.objects.get(id=show_version_id,
                                                          template_id=template.id)
            show_version.update_real_version_id(real_version_id, updator=username)
        
        del create_data['template']
        client.ContextActivityLogClient(
            project_id=create_data['project_id'],
            user=username,
            resource_type="template",
            resource=template.name,
            resource_id=template.id,
            extra=json.dumps(create_data),
            description=f"新建版本[{show_version_name}]" if show_version_id == 0 else f"更新版本[{show_version_name}]"
        ).log_modify()

        return show_version

    def get_resource_config(self, request, project_id, template_id, show_version_id):
        serializer = GetShowVersionSLZ(data=self.kwargs)
        serializer.is_valid(raise_exception=True)
        validated_data = serializer.validated_data

        template = validated_data['template']
        self.can_view_template(request, template)

        serializer = ResourceConfigSLZ(validated_data)
        return Response(serializer.data)

    def list_show_versions(self, request, project_id, template_id):
        template = models.get_template_by_project_and_id(project_id, template_id)
        self.can_view_template(request, template)

        show_versions = models.ShowVersion.objects.filter(template_id=template.id)
        serializer = ListShowVersionSLZ(show_versions, many=True)
        show_version_list = list(serializer.data)

        if request.GET.get('is_filter_draft') != '1':
            if template.get_draft():
                show_version_list.append(get_draft_show_version(template))

        show_version_list.sort(key=lambda k: k['updated'], reverse=True)
        return Response(show_version_list)

    def list_show_versions_for_instance(self, request, project_id, template_id):
        """根据模板集查询用户可见版本列表
        实例化页面查询模板集版本列表
        """
        template = models.get_template_by_project_and_id(project_id, template_id)
        self.can_use_template(request, template)
        show_versions = models.ShowVersion.objects.filter(template_id=template.id)
        serializer = ListShowVersionISLZ(show_versions, many=True)
        return Response({'results': serializer.data})

    def save_with_ventity(self, request, project_id, template_id):
        """保存用户可见的版本信息
        """
        data = request.data
        data.update({
            'project_id': project_id,
            'template_id': template_id
        })
        serializer = ShowVersionWithEntitySLZ(data=data)
        serializer.is_valid(raise_exception=True)
        validated_data = serializer.validated_data

        template = validated_data['template']
        self.can_edit_template(request, template)

        create_data = validated_data
        create_data['username'] = request.user.username
        show_version = self._create_or_update_with_ventity(create_data)

        # model Template updated field need change when save version
        template.save(update_fields=['updated'])

        return Response({'show_version_id': show_version.id, 'real_version_id': show_version.real_version_id})

    def save_without_ventity(self, request, project_id, template_id):
        """仅仅创建可见版本
        """
        template = models.get_template_by_project_and_id(project_id, template_id)
        self.can_edit_template(request, template)

        serializer = ShowVersionCreateSLZ(data=request.data)
        serializer.is_valid(raise_exception=True)

        username = request.user.username
        show_version = models.ShowVersion.objects.create(
            template_id=template.id,
            name=serializer.data['name'],
            creator=username,
            updator=username,
        )

        # model Template updated field need change when save version
        template.save(update_fields=['updated'])

        return Response({'show_version_id': show_version.id})

    def _delete_show_version(self, delete_data):
        project_id = delete_data['project_id']
        template = delete_data['template']
        show_version_id = delete_data['show_version_id']
        username = delete_data['username']

        if show_version_id == '-1':
            models.Template.objects.filter(id=template.id).update(
                draft='',
                draft_time=None,
                draft_updator='',
                draft_version=0
            )
            client.ContextActivityLogClient(
                project_id=project_id,
                user=username,
                resource_type="template",
                resource=template.name,
                resource_id=template.id,
                extra='',
                description="删除草稿"
            ).log_modify()
        else:
            show_version = models.ShowVersion.objects.get(template_id=template.id, id=show_version_id)
            version_name = show_version.name
            show_version.delete()

            client.ContextActivityLogClient(
                project_id=project_id,
                user=username,
                resource_type="template",
                resource=template.name,
                resource_id=template.id,
                extra=show_version_id,
                description=f"删除版本[{version_name}]"
            ).log_modify()

    def delete_show_version(self, request, project_id, template_id, show_version_id):
        template = models.get_template_by_project_and_id(project_id, template_id)
        # 已经实例化过的版本不能被删除
        has_instance = has_instance_of_show_version(template.id, show_version_id)
        if has_instance:
            raise ValidationError("该版本已经被实例化过，不能被删除")

        self.can_edit_template(request, template)

        delete_data = {
            'project_id': project_id,
            'template': template,
            'show_version_id': show_version_id,
            'username': request.user.username
        }
        self._delete_show_version(delete_data)
        return Response({'show_version_id': show_version_id})

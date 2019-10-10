#!/usr/bin/python
# Copyright (c) 2019 OpenStack Foundation
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
__metaclass__ = type

ANSIBLE_METADATA = {'metadata_version': '1.1',
                    'status': ['preview'],
                    'supported_by': 'community'}


DOCUMENTATION = '''
---
module: os_tripleo_baremetal_node_introspection
short_description: Introspect Ironic nodes
extends_documentation_fragment: openstack
author:
  - "Dougal Matthews"
  - "Sagi Shnaidman"
version_added: "2.10"
description:
    - Requests Ironic for nodes info.
options:
    ironic_url:
      description:
        - If noauth mode is utilized, this is required to be set to the
          endpoint URL for the Ironic API.
          Use with "auth" and "auth_type" settings set to None.
      type: str
      required: False
    node_uuids:
      description:
        - node_uuids
      type: list
      required: True
    concurrency:
      description:
        - concurrency
      type: int
      default: 20
    max_retries:
      description:
        - max_retries
      type: int
      default: 2
    node_timeout:
      description:
        - node_timeout
      type: int
      default: 1200
    quiet:
      descriptio:
        - Don't provide instrospection info in output of the module
      type: bool
      default: False
'''

RETURN = '''
introspection_data:
    description: Dictionary of new facts representing introspection data of
                 nodes.
    returned: changed
    type: dict
    sample: {
        "400b3cd0-d134-417b-8f0e-63e273e01e5a": {
            "failed": false,
            "retries": 0,
            "status": {
                "error": null,
                "finished_at": "2019-11-22T01:09:07",
                "id": "400b3cd0-d134-417b-8f0e-63e273e01e5a",
                "is_finished": true,
                "links": [
                    {
                        "href": "http://192.168.24.2:13050 .... ",
                        "rel": "self"
                    }
                ],
                "location": {
                    "cloud": "undercloud",
                    "project": {
                        "domain_id": null,
                        "domain_name": "Default",
                        "id": "......",
                        "name": "admin"
                    },
                    "region_name": "regionOne",
                    "zone": null
                },
                "name": null,
                "started_at": "2019-11-22T01:07:32",
                "state": "finished"
            }
        }
    }
'''

EXAMPLES = '''
# Invoke node introspection

- os_tripleo_baremetal_node_introspection:
    cloud: undercloud
    auth: password
    node_uuids:
      - uuid1
      - uuid2
    concurrency: 10
    max_retries: 1
    node_timeout: 1000

'''

from concurrent import futures
import time
import yaml

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.openstack import openstack_full_argument_spec
from ansible.module_utils.openstack import openstack_module_kwargs
from ansible.module_utils.openstack import openstack_cloud_from_module


class IntrospectNodes(object):
    def __init__(self,
                 cloud,
                 module,
                 concurrency,
                 max_retries,
                 node_timeout):
        self.client = cloud.baremetal_introspection
        self.module = module
        self.concurrency = concurrency
        self.max_retries = max_retries
        self.node_timeout = node_timeout

    def log(self, msg):
        self.module.log("os_tripleo_baremetal_node_introspection: %s" % msg)

    def _introspect_node(self, node_uuid):
        retry_count = 0
        status = None

        while retry_count <= self.max_retries:

            if retry_count == 0:
                self.log("INFO Starting introspection %s" % node_uuid)
            else:
                self.log(
                    "INFO Restarting introspection %s (retry %s of %s)"
                    % (node_uuid, retry_count, self.max_retries)
                )

            try:
                introspection = self.client.start_introspection(node_uuid)
                node_result = self.client.wait_for_introspection(
                    introspection, timeout=self.node_timeout)
            except Exception as exc:
                retry_count += 1
                error_msg = "Introspection of node %s failed: %s" % (
                    node_uuid,
                    str(exc),
                )
                self.log("ERROR %s" % error_msg)
                status = {"error": error_msg}
                # Added timeout here, it's impossible to retry
                # introspection immediately, need to wait a little
                time.sleep(15)
                continue

            status = dict(node_result)
            if status["error"] is not None:
                retry_count += 1
                self.log(
                    "ERROR Introspection of node %s failed: %s"
                    % (node_uuid, status["error"])
                )
                continue
            self.log("INFO Finished introspection of node %s" % node_uuid)

            return {"retries": retry_count, "status": status, "failed": False}
        self.log("ERROR Retry limit reached for node %s" % node_uuid)
        return {"retries": retry_count, "status": status, "failed": True}

    def introspect(self, node_uuids):

        result = {}

        with futures.ThreadPoolExecutor(
                max_workers=self.concurrency) as executor:
            future_to_uuid = {
                executor.submit(self._introspect_node, node_uuid): node_uuid
                for node_uuid in node_uuids
            }
            for future in futures.as_completed(future_to_uuid):
                uuid = future_to_uuid[future]
                try:
                    result[uuid] = future.result()
                except Exception as exc:
                    self.log("ERROR Unhandled error introspecting %s: %s" % (
                        uuid, str(exc))
                    )
                    result[uuid] = {"error": str(exc)}

        return result


def main():
    argument_spec = openstack_full_argument_spec(
        **yaml.safe_load(DOCUMENTATION)['options']
    )
    module_kwargs = openstack_module_kwargs()
    module = AnsibleModule(
        argument_spec,
        supports_check_mode=False,
        **module_kwargs
    )
    auth_type = module.params.get('auth_type')
    ironic_url = module.params.get('ironic_url')
    if auth_type in (None, 'None'):
        if not ironic_url:
            module.fail_json(
                msg="Authentication appears to be disabled,"
                    " Please define an ironic_url parameter"
            )
        else:
            module.params['auth'] = {'endpoint': ironic_url}

    _, cloud = openstack_cloud_from_module(module)

    introspector = IntrospectNodes(
        cloud,
        module,
        module.params["concurrency"],
        module.params["max_retries"],
        module.params["node_timeout"]
    )
    module_results = {"changed": True}
    result = introspector.introspect(module.params["node_uuids"])
    failed_nodes = [k for k, v in result.items() if v['failed']]
    passed_nodes = [k for k, v in result.items() if not v['failed']]
    failed = len(failed_nodes)
    if failed > 0:
        message = ("Introspection completed with failures. %s node(s) failed."
                   % failed)
        module.log("os_tripleo_baremetal_node_introspection ERROR %s" %
                   message)
        module_results.update({'failed': True})
    else:
        message = "Introspection completed successfully: %s nodes" % len(
            module.params["node_uuids"])
        module.log("os_tripleo_baremetal_node_introspection INFO %s" %
                   message)

    module_results.update({
        "introspection_data": result if not module.params['quiet'] else {},
        "failed_nodes": failed_nodes,
        "passed_nodes": passed_nodes,
        "msg": message
    })
    module.exit_json(**module_results)


if __name__ == "__main__":
    main()

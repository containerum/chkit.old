#!/usr/bin/python3
import os
import json
import yaml
import datetime
from data import kinds, output_formats, deployment_json, service_json
from parser import *
from tcp_handler import TcpHandler
from api_handler import ApiHandler
from webclient_api_handler import WebClient
from bcolors import BColors
from config_json_handler import get_json_from_config, set_token_to_json_config,set_default_namespace_to_json_config
from answer_parsers import TcpApiParser, WebClientApiParser
import uuid
from keywords import JSON_TEMPLATES_RUN_FILE
from run_configure import RunConfigure


config_json_data = get_json_from_config()


class Client:
    def __init__(self):
        self.path = os.getcwd()
        self.version = config_json_data.get("version")
        self.parser = create_parser(kinds, output_formats, self.version)
        uuid_v4 = str(uuid.uuid4())
        self.args = vars(self.parser.parse_args())
        self.debug = self.args.get("debug")
        self.tcp_handler = TcpHandler(uuid_v4, self.args.get("debug"))
        self.api_handler = ApiHandler(uuid_v4)

    def modify_config(self):
        if self.args.get("set_token"):
            set_token_to_json_config(self.args.get("set_token"))
        if self.args.get("set_default_namespace"):
            set_default_namespace_to_json_config(self.args.get("set_default_namespace"))

    def logout(self):
        set_token_to_json_config("")
        print("Bye!")

    def go(self):
        self.check_file_existence()
        self.check_arguments()

        if self.args.get("kind") in ("deployments", "deploy", "deployment"):
            self.args["kind"] = "deployments"
        elif self.args.get("kind") in ("po", "pods", "pod"):
            self.args["kind"] = "pods"
        elif self.args.get("kind") in ("service", "services", "svc"):
            self.args["kind"] = "services"
        else:
            self.args["kind"] = "namespaces"

        if self.args['command'] == 'run':
            self.go_run()

        elif self.args['command'] == 'create':
            self.go_create()

        elif self.args['command'] == 'get':
            self.go_get()

        elif self.args['command'] == 'delete':
            self.go_delete()

        elif self.args['command'] == 'replace':
            self.go_replace()

        elif self.args['command'] == 'config':
            self.modify_config()

        elif self.args['command'] == 'expose':
            self.go_expose()

        elif self.args['command'] == 'logout':
            self.logout()

    def go_run(self):
        json_to_send = self.construct_run()
        if self.debug:
            self.log_time()

        self.tcp_connect()

        api_result = self.api_handler.run(json_to_send)
        self.handle_api_result(api_result)
        self.get_and_handle_tcp_result('run')

        self.tcp_handler.close()

    def go_expose(self):
        json_to_send = self.construct_expose()
        if self.debug:
            self.log_time()

        self.tcp_connect()
        namespace = self.args.get('namespace')
        if not namespace:
            namespace = config_json_data.get("default_namespace")

        api_result = self.api_handler.expose(json_to_send, namespace)
        self.handle_api_result(api_result)

        self.get_and_handle_tcp_result('expose')

        self.tcp_handler.close()

    def go_create(self):
        if self.args.get("debug"):
            self.log_time()
        self.tcp_connect()

        json_to_send = self.get_json_from_file()
        kind = '{}s'.format(json_to_send.get('kind')).lower()

        namespace = self.args.get('namespace')
        if not namespace:
            namespace = config_json_data.get("default_namespace")

        if kind != 'namespaces':
            api_result = self.api_handler.create(json_to_send, namespace)
        else:
            api_result = self.api_handler.create_namespaces(json_to_send)
        self.handle_api_result(api_result)

        self.get_and_handle_tcp_result('create')

        self.tcp_handler.close()

    def go_get(self):
        kind, name = self.construct_get()
        if self.debug:
            self.log_time()
        self.tcp_connect()

        namespace = self.args.get('namespace')
        if not namespace:
            namespace = config_json_data.get("default_namespace")

        if kind == "namespaces":
            if self.args.get("name"):
                api_result = self.api_handler.get_namespaces(self.args.get("name")[0])
            else:
                api_result = self.api_handler.get_namespaces()
        else:
            api_result = self.api_handler.get(kind, name, namespace)
        self.handle_api_result(api_result)
        json_result = self.get_and_handle_tcp_result('get')
        self.tcp_handler.close()
        return json_result

    def get_and_handle_tcp_result(self, command_name, wide=False):
        try:
            tcp_result = self.tcp_handler.receive()
            if command_name == 'get':
                if not tcp_result.get('status') == 'Failure':
                    if self.args.get("debug"):

                        print('{}{}{}'.format(
                            BColors.OKBLUE,
                            'get result:\n',
                            BColors.ENDC
                        ))
                    self.print_result(tcp_result)

            self.print_result_status(tcp_result, command_name)
            return tcp_result

        except RuntimeError as e:
            print('{}{}{}'.format(
                BColors.FAIL,
                e,
                BColors.ENDC
            ))
            return None

    def go_delete(self):
        kind, name = self.construct_delete()

        self.log_time()
        self.tcp_connect()

        self.args['output'] = 'yaml'
        namespace = self.args['namespace']
        if not namespace:
            namespace = config_json_data.get("default_namespace")
        if kind != 'namespaces':
            api_result = self.api_handler.delete(kind, name, namespace)
        else:
            api_result = self.api_handler.delete_namespaces(name)
        self.handle_api_result(api_result)

        self.get_and_handle_tcp_result('delete')

        self.tcp_handler.close()

    def go_replace(self):
        self.log_time()
        self.tcp_connect()

        self.args['output'] = 'yaml'
        namespace = self.args['namespace']

        json_to_send = self.get_json_from_file()
        kind = '{}s'.format(json_to_send.get('kind')).lower()

        if kind != 'namespaces':
            api_result = self.api_handler.replace(json_to_send, namespace)
        else:
            api_result = self.api_handler.replace_namespaces(json_to_send)
        self.handle_api_result(api_result)

        self.get_and_handle_tcp_result('replace')

        self.tcp_handler.close()

    def check_file_existence(self):
        if 'file' in self.args:
            if self.args.get('file'):
                if not os.path.isfile(os.path.join(self.path, self.args.get('file'))):
                    self.parser.error('no such file: {}'.format(
                        os.path.join(self.path, self.args.get('file'))
                    ))

    def check_arguments(self):
        if not (self.args.get('version') or self.args.get('help') or self.args.get('command')):
            self.parser.print_help()

    def handle_api_result(self, api_result):
        if api_result.get('id') and self.debug:
            print('{}{}...{} {}OK{}'.format(
                BColors.OKBLUE,
                'api connection',
                BColors.ENDC,
                BColors.BOLD,
                BColors.ENDC
            ))
        elif 'error' in api_result:
            print('{}api error: {}{}'.format(
                BColors.FAIL,
                api_result.get('error'),
                BColors.ENDC
            ))
            self.tcp_handler.close()
            exit()

    def tcp_connect(self):
        try:
            tcp_auth_result = self.tcp_handler.connect()
            if tcp_auth_result.get('ok') and self.debug:
                # print(tcp_auth_result)
                print('{}{}...{} {}OK{}'.format(
                    BColors.OKBLUE,
                    'tcp authorization',
                    BColors.ENDC,
                    BColors.BOLD,
                    BColors.ENDC
                ))
        except RuntimeError as e:
            print('{}{}{}'.format(
                BColors.FAIL,
                e,
                BColors.ENDC
            ))

    def print_result_status(self, result, message):
        if result.get('status') == 'Failure':
            print('{}error: {}{}'.format(
                BColors.FAIL,
                result.get('message'),
                BColors.ENDC
            ))


        elif self.args["command"] != "get":
            print('{}{}...{} {}OK{}'.format(
                BColors.WARNING,
                message,
                BColors.ENDC,
                BColors.BOLD,
                BColors.ENDC
            ))

    def print_result(self, result):
        if self.args.get("command") != "expose":
            if self.args.get('output') == 'yaml':
                print(yaml.dump(result, default_flow_style=False))
            elif self.args['output'] == 'json':
                print(json.dumps(result, indent=4))
            else:
                TcpApiParser(result)

    def log_time(self):
        print('{}{}{}'.format(
            BColors.WARNING,
            str(datetime.datetime.now())[11:19:],
            BColors.ENDC
        ))

    def construct_run(self):
        if self.args.get("kind") in ("deploy", "deployment", "deployments"):
            json_to_send = deployment_json
            json_to_send['metadata']['name'] = self.args['name']
            if self.args["configure"] and not self.args.get("iamge"):
                runconfigure = RunConfigure()
                param_dict = runconfigure.get_data_from_console()
                image = param_dict["image"]
                ports = param_dict["ports"]
                labels = param_dict["labels"]
                env = param_dict["env"]
                cpu = param_dict["cpu"]
                memory = param_dict["memory"]
                replicas = param_dict["replicas"]
                commands = param_dict["commands"]

            elif self.args.get("image") and not self.args["configure"]:
                image = self.args["image"]
                ports = self.args["ports"]
                labels = self.args["labels"]
                env = self.args["env"]
                cpu = self.args["cpu"]
                memory = self.args["memory"]
                replicas = self.args["replicas"]
                commands = self.args["commands"]

            if self.args["configure"] or self.args["image"]:
                json_to_send['spec']['replicas'] = replicas
                json_to_send['spec']['template']['metadata']['labels']['run'] = self.args['name']
                json_to_send['spec']['template']['metadata']['name'] = self.args['name']
                json_to_send['spec']['template']['spec']['containers'][0]['name'] = self.args['name']
                json_to_send['spec']['template']['spec']['containers'][0]['image'] = image
                if commands:
                    json_to_send['spec']['template']['spec']['containers'][0]['command'] = commands
                if ports:
                    json_to_send['spec']['template']['spec']['containers'][0]['ports'] = []
                    for port in ports:
                        json_to_send['spec']['template']['spec']['containers'][0]['ports'].append({
                            'containerPort': port
                        })

                if labels:
                    for label in labels:
                        key, value = label.split("=")
                        json_to_send['metadata']['labels'].update({key: value})
                if env:
                    json_to_send['spec']['template']['spec']['containers'][0]['env'] = [
                        {
                            "name": key_value.split('=')[0],
                            "value": key_value.split('=')[1]
                        }
                        for key_value in env]
                json_to_send['spec']['template']['spec']['containers'][0]['resources']["requests"]['cpu'] = cpu
                json_to_send['spec']['template']['spec']['containers'][0]['resources']["requests"]['memory'] = memory
                with open(os.path.join(os.getenv("HOME") + "/.containerum/src/", JSON_TEMPLATES_RUN_FILE), 'w', encoding='utf-8') as w:
                    json.dump(json_to_send, w, indent=4)

                return json_to_send

    def get_json_from_file(self):
        file_name = os.path.join(self.path, self.args['file'])
        try:
            with open(file_name, 'r', encoding='utf-8') as f:
                body = json.load(f)
                return body
        except FileNotFoundError:
            self.parser.error('no such file: {}'.format(
                file_name
            ))
        except json.decoder.JSONDecodeError as e:
            self.parser.error('bad json: {}'.format(
                e
            ))

    def construct_delete(self):
        if self.args['file'] and not self.args['kind'] and not self.args['name']:
            body = self.get_json_from_file()
            name = body['metadata']['name']
            kind = '{}s'.format(body['kind'].lower())
            return kind, name

        elif not self.args['file'] and self.args['kind'] and self.args['name']:
            kind = self.args['kind']
            name = self.args['name']
            return kind, name

        elif not self.args['file'] and not self.args['kind']:
            self.parser.error(ONE_REQUIRED_ARGUMENT_ERROR)
        elif self.args['file'] and self.args['kind']:
            self.parser.error(KIND_OR_FILE_BOTH_ERROR)
        elif self.args['file'] and self.args['name']:
            self.parser.error(NAME_OR_FILE_BOTH_ERROR)
        elif self.args['kind'] and not self.args['name']:
            self.parser.error(NAME_WITH_KIND_ERROR)

    def construct_get(self):
        if self.args.get('file') and not self.args['kind'] and not self.args['name']:
            body = self.get_json_from_file()
            name = body['metadata']['name']
            kind = '{}s'.format(body['kind'].lower())
            return kind, name

        elif not self.args.get('file') and self.args['kind']:
            kind = self.args['kind']
            name = self.args['name']
            return kind, name

        elif not self.args.get('file') and not self.args['kind']:
            self.parser.error(ONE_REQUIRED_ARGUMENT_ERROR)
        elif self.args['file'] and self.args['kind']:
            self.parser.error(KIND_OR_FILE_BOTH_ERROR)
        elif self.args['file'] and self.args['name']:
            self.parser.error(NAME_OR_FILE_BOTH_ERROR)

    def construct_expose(self):
        json_to_send = service_json
        ports = self.args.get("ports")
        if ports:
            for p in ports:
                p = p.split(":")
                if len(p) == 3:
                    json_to_send["spec"]["ports"].append({"name": p[0], "protocol": p[2], "targetPort": int(p[1])})
                elif len(p) == 2:
                    json_to_send["spec"]["ports"].append({"name": p[0], "protocol": "TCP", "targetPort": int(p[1])})
        result = self.go_get()
        labels = result.get("results")[0].get("data")\
            .get("spec").get("template").get("metadata").get("labels")
        json_to_send["metadata"]["labels"] = labels
        json_to_send["metadata"]["name"] = self.args["name"][0]
        json_to_send["spec"]["selector"].update({"app": self.args["name"][0]})
        print(json_to_send)
        return json_to_send


def main():
    client = Client()
    client.go()


if __name__ == '__main__':
    main()

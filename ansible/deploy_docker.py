#!/usr/bin/env python

import argparse
import collections
import docker
import os
import six
import sys
from distutils.version import LooseVersion

if not (LooseVersion('1.9') <= LooseVersion(docker.version) < LooseVersion('2')):
    raise Exception('docker-py must be >= version 1.9 and < version 2')


BaseName = 'histomicstk'
ImageList = collections.OrderedDict([
    ('rmq', {
        'tag': 'rabbitmq:management',
        'name': 'histomicstk_rmq',
        'pull': True,
    }),
    ('mongodb', {
        'tag': 'mongo:latest',
        'name': 'histomicstk_mongodb',
        'pull': True,
    }),
    ('worker', {
        'tag': 'dsarchive/girder_worker',
        'name': 'histomicstk_girder_worker',
        'dockerfile': 'Dockerfile-girder-worker',
    }),
    ('histomicstk', {
        'tag': 'dsarchive/histomicstk_main',
        'name': 'histomicstk_histomicstk',
        'dockerfile': 'Dockerfile-histomicstk',
    }),
])


def containers_start(port=8080, rmq='docker', mongo='docker',  # noqa
                     mongodb_path='docker', provision=False, **kwargs):
    """
    Start all appropriate containers.  This is, at least, girder_worker and
    histomicstk.  Optionally, mongodb and rabbitmq are included.

    :param port: default port to expose.
    :param rmq: 'docker' to use a docker for rabbitmq, 'host' to use the docker
        host, otherwise the IP for the rabbitmq instance, where DOCKER_HOST
        maps to the docker host and anything else is passed through.
    :param mongo: 'docker' to use a docker for mongo, 'host' to use the docker
        host, otherwise the IP for the mongo instance, where DOCKER_HOST maps
        to the docker host and anything else is passed through.  The database
        is always 'girder'.
    :param mongodb_path: the path to use for mongo when run in docker.  If
        'docker', use an internal data directory.
    :param provision: if True, reprovision after starting.  Otherwise, only
        provision if the histomictk container is created.
    """
    client = docker.from_env()
    env = {}

    network_create(client, BaseName)

    if rmq == 'docker':
        key = 'rmq'
        image = ImageList[key]['tag']
        name = ImageList[key]['name']
        ctn = get_docker_image_and_container(client, key)
        if ctn is None:
            config = {
                'restart_policy': {'name': 'always'},
            }
            params = {
                'image': image,
                'detach': True,
                'hostname': key,
                'name': name,
                # 'ports': [15672],  # for management access
            }
            print('Creating %s - %s' % (image, name))
            ctn = client.create_container(
                host_config=client.create_host_config(**config),
                networking_config=client.create_networking_config({
                    BaseName: client.create_endpoint_config(aliases=[key])
                }),
                **params)
        print('Starting %s - %s' % (image, name))
        client.start(container=ctn.get('Id'))
    else:
        env['HOST_RMQ'] = 'true'
        # If we generate the girder worker config file on the fly, update this
        # to something like:
        # env['HOST_RMQ'] = rmq if rmq != 'host' else 'DOCKER_HOST'

    if mongo == 'docker':
        key = 'mongodb'
        image = ImageList[key]['tag']
        name = ImageList[key]['name']
        ctn = get_docker_image_and_container(client, key)
        if ctn is None:
            config = {
                'restart_policy': {'name': 'always'},
            }
            params = {
                'image': image,
                'detach': True,
                'hostname': key,
                'name': name,
            }
            if mongodb_path != 'docker':
                params['volumes'] = ['/data/db']
                config['binds'] = [
                    get_path(mongodb_path) + ':/data/db:rw',
                ]
            print('Creating %s - %s' % (image, name))
            ctn = client.create_container(
                host_config=client.create_host_config(**config),
                networking_config=client.create_networking_config({
                    BaseName: client.create_endpoint_config(aliases=[key])
                }),
                **params)
            os.system('docker update --restart=always %s' % ctn.get('Id'))
        print('Starting %s - %s' % (image, name))
        client.start(container=ctn.get('Id'))
    else:
        env['HOST_MONGO'] = 'true'
        # If we generate the girder worker config file on the fly, update this
        # to something like:
        # env['HOST_MONGO'] = mongo if mongo != 'host' else 'DOCKER_HOST'

    key = 'worker'
    image = ImageList[key]['tag']
    name = ImageList[key]['name']
    ctn = get_docker_image_and_container(client, key)
    if ctn is None:
        config = {
            'restart_policy': {'name': 'always'},
            'privileged': True,  # so we can run docker
            'links': {},
            'binds': [
                get_path(kwargs['logs']) + ':/opt/logs:rw',
                '/usr/bin/docker:/usr/bin/docker',
                '/var/run/docker.sock:/var/run/docker.sock',
            ]
        }
        if rmq == 'docker':
            config['links'][ImageList['rmq']['name']] = 'rmq'
        params = {
            'image': image,
            'detach': True,
            'hostname': key,
            'name': name,
            'environment': env.copy(),
            'volumes': [
                '/opt/logs',
                '/usr/bin/docker',
                '/var/run/docker.sock',
            ]
        }
        print('Creating %s - %s' % (image, name))
        ctn = client.create_container(
            host_config=client.create_host_config(**config),
            networking_config=client.create_networking_config({
                BaseName: client.create_endpoint_config(aliases=[key])
            }),
            **params)
        os.system('docker update --restart=always %s' % ctn.get('Id'))
    print('Starting %s - %s' % (image, name))
    client.start(container=ctn.get('Id'))

    key = 'histomicstk'
    image = ImageList[key]['tag']
    name = ImageList[key]['name']
    ctn = get_docker_image_and_container(client, key)
    if ctn is None:
        provision = True
        config = {
            'restart_policy': {'name': 'always'},
            'privileged': True,  # so we can run docker
            'links': {},
            'port_bindings': {8080: int(port)},
            'binds': [
                get_path(kwargs['logs']) + ':/opt/logs:rw',
                get_path(kwargs['logs']) + ':/opt/histomicstk/logs:rw',
                get_path(kwargs['assetstore']) + ':/opt/histomicstk/assetstore:rw',
                '/usr/bin/docker:/usr/bin/docker',
                '/var/run/docker.sock:/var/run/docker.sock',
            ],
        }
        if rmq == 'docker':
            config['links'][ImageList['rmq']['name']] = 'rmq'
        if mongo == 'docker':
            config['links'][ImageList['mongodb']['name']] = 'mongodb'
        params = {
            'image': image,
            'detach': True,
            'hostname': key,
            'name': name,
            'environment': env.copy(),
            'ports': [8080],
            'volumes': [
                '/opt/logs',
                '/opt/histomicstk/assetstore',
                '/opt/histomicstk/logs',
                '/usr/bin/docker',
                '/var/run/docker.sock',
            ],
        }
        print('Creating %s - %s' % (image, name))
        ctn = client.create_container(
            host_config=client.create_host_config(**config),
            networking_config=client.create_networking_config({
                BaseName: client.create_endpoint_config(aliases=[key])
            }),
            **params)
        os.system('docker update --restart=always %s' % ctn.get('Id'))
    print('Starting %s - %s' % (image, name))
    client.start(container=ctn.get('Id'))

    if provision:
        # docker exec -i -t histomicstk_histomicstk bash -c
        # 'cd /home/ubuntu/HistomicsTK/ansible && ansible-playbook -i
        # inventory/local docker_ansible.yml --extra-vars=docker=provision'
        tries = 1
        while True:
            cmd = client.exec_create(
                container=ctn.get('Id'),
                cmd="bash -c 'cd /home/ubuntu/HistomicsTK/ansible && "
                    "ansible-playbook -i inventory/local docker_ansible.yml "
                    "--extra-vars=docker=provision'",
                tty=True,
            )
            for output in client.exec_start(cmd.get('Id'), stream=True):
                print(output.strip())
            cmd = client.exec_inspect(cmd.get('Id'))
            if not cmd['ExitCode']:
                break
            print('Error provisioning (try %d)' % tries)
            tries += 1
            if not kwargs.get('retry'):
                raise Exception('Failed to provision')

    # import pprint
    # pprint.pprint(client.containers(all=True))


def containers_stop(remove=False, **kwargs):
    """"
    Stop and optionally remove any containers we are responsible for.

    :param remove: True to remove the containers.  False to just stop them.
    """
    client = docker.from_env()
    keys = ImageList.keys()
    keys.reverse()
    for key in keys:
        ctn = get_docker_image_and_container(client, key, False)
        if ctn:
            if ctn.get('State') != 'exited':
                print('Stopping %s' % (key))
                client.stop(container=ctn.get('Id'))
            if remove:
                print('Removing %s' % (key))
                client.remove_container(container=ctn.get('Id'))

    if remove:
        network_remove(client, BaseName)


def get_docker_image_and_container(client, key, pullOrBuild=True):
    """
    Given a key from the docker ImageList, check if an image is present.  If
    not, pull it.  Check if an associated container exists and return
    information on it if so.

    :param client: docker client.
    :param key: key in the ImageList.
    :param pullOrBuild: if True, try to pull or build the image if it isn't
        present.
    :returns: docker container or None.
    """
    name = ImageList[key]['name']
    if pullOrBuild:
        image = ImageList[key]['tag']
        try:
            client.inspect_image(image)
        except docker.errors.NotFound:
            print('Pulling %s' % image)
            try:
                client.pull(image)
            except 'foo':
                if not ImageList[key].get('pull'):
                    images_build(True, key)
    containers = client.containers(all=True)
    ctn = [entry for entry in containers if name in
           [val.strip('/') for val in entry.get('Names', [])]]
    if len(ctn):
        return ctn[0]
    return None


def get_path(path):
    """
    Resolve a path to its realpath, creating a directory there if it doesn't
    exist.

    :param path: path to resolve and possibly create.
    :return: the resolved path.
    """
    path = os.path.realpath(os.path.expanduser(path))
    if not os.path.exists(path):
        os.makedirs(path)
    return path


def images_build(retry=False, names=None):
    r"""
    Build necessary docker images from our dockerfiles.

    This is equivalent to running:
    docker build --force-rm --tag dsarchive/girder_worker \
           -f Dockerfile-girder-worker .
    docker build --force-rm --tag dsarchive/histomicstk_main \
           -f Dockerfile-histomicstk .

    :param retry: True to retry until success
    :param names: None to build all, otherwise a string or a list of strings of
        names to build.
    """
    basepath = os.path.dirname(os.path.realpath(__file__))
    client = docker.from_env()

    if names is None:
        names = ImageList.keys()
    elif isinstance(names, six.string_types):
        names = [names]
    for name in ImageList:
        if not ImageList[name].get('dockerfile') or name not in names:
            continue
        tries = 1
        while True:
            errored = False
            print('Building %s%s' % (
                name, '(try %d)' % tries if tries > 1 else ''))
            buildStatus = client.build(
                path=basepath,
                tag=ImageList[name]['tag'],
                rm=True,
                pull=True,
                forcerm=True,
                dockerfile=ImageList[name]['dockerfile'],
                decode=True,
            )
            for status in buildStatus:
                print(status.get('status', status.get('stream', '')).strip())
                if 'errorDetail' in status:
                    if not retry:
                        sys.exit(1)
                    errored = True
                    break
            if not errored:
                break
            print('Error building %s\n' % name)
            tries += 1
        print('Done building %s\n' % name)


def network_create(client, name):
    """
    Ensure a network exists with a specified name.

    :param client: docker client.
    :param name: name of the network.
    """
    networks = client.networks()
    net = [entry for entry in networks if name == entry.get('Name')]
    if len(net):
        return
    client.create_network(name)


def network_remove(client, name):
    """
    Ensure a network with a specified name is removed.

    :param client: docker client.
    :param name: name of the network.
    """
    networks = client.networks()
    net = [entry for entry in networks if name == entry.get('Name')]
    if not len(net):
        return
    client.remove_network(net[0].get('Id'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Provision and run HistomicsTK in docker containers.')
    parser.add_argument(
        'command',
        choices=['start', 'restart', 'stop', 'rm', 'remove', 'status',
                 'build', 'provision'],
        help='Start, stop, stop and remove, restart, check the status of, or '
             'build our own docker containers')
    parser.add_argument(
        '--assetstore', '-a', default='~/.histomicstk/assetstore',
        help='Assetstore path.')
    parser.add_argument(
        '--build', '-b', dest='build', action='store_true',
        help='Build gider_worker and histomicstk docker images.')
    parser.add_argument(
        '--db', '-d', dest='mongodb_path', default='~/.histomicstk/db',
        help='Database path (if a Mongo docker container is used).  Use '
             '"docker" for the default docker storage location.')
    parser.add_argument(
        '--logs', '--log', '-l', default='~/.histomicstk/logs',
        help='Logs path.')
    parser.add_argument(
        '--mongo', '-m', default='docker',
        choices=['docker', 'host'],
        help='Either use mongo from docker or from host.')
    parser.add_argument(
        '--provision', action='store_true',
        help='Reprovision the Girder the docker containers are started.')
    parser.add_argument(
        '--port', '-p', type=int, default=8080,
        help='Girder access port.')
    parser.add_argument(
        '--retry', '-r', action='store_true',
        help='Retry builds and provisioning until they succeed')
    parser.add_argument(
        '--rmq', default='docker',
        choices=['docker', 'host'],
        help='Either use rabbitmq from docker or from host.')
    parser.add_argument(
        '--status', '-s', action='store_true',
        help='Report the status of relevant docker containers and images.')

    # Should we add an optional url or host value for rmq and mongo?
    # Should we allow installing packages in a local directory to make it
    #   easier to develop python and javascript?
    # We should show how to run the ctests
    # Add a provisioning step to copy sample data (possibly by mounting the
    #   appropriate host directory).
    # Add status command

    args = parser.parse_args()

    if args.command == 'provision':
        args.command = 'start'
        args.provision = True

    if args.build or args.command == 'build':
        images_build(args.retry)
    if args.command in ('stop', 'restart', 'rm'):
        containers_stop(remove=args.command in ('remove', 'rm'))
    if args.command in ('start', 'restart'):
        containers_start(**vars(args))
    # if args.command in ('status', ) or args.status:
    #     containers_status(**vars(args))

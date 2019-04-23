#!/usr/bin/env python3
"""
Paloaltonetworks deploy.py

This software is provided without support, warranty, or guarantee.
Use at your own risk.

Usage

python deploy.py -u <fwusername> -p'<fwpassword>

Contents of json dict

{"WebInDeploy": "Success", "WebInFWConf": "Success", "waf_conf": "Success"}
`"""

import argparse
import json
import logging
import ssl
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import os
import uuid
import requests
import urllib3
import xmltodict
from azure.common import AzureException
from azure.storage.file import FileService
# from . import cache_utils
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from pandevice import firewall
from python_terraform import Terraform
from collections import OrderedDict

_archive_dir = './WebInDeploy/bootstrap'
_content_update_dir = './WebInDeploy/content_updates/'

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()
handler = logging.StreamHandler()
formatter = logging.Formatter('%(levelname)-8s %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)

# global var to keep status output
status_output = dict()


def send_request(call):

    headers = {'Accept-Encoding' : 'None',
               'User-Agent' : 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_10_1) '
                              'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/39.0.2171.95 Safari/537.36'}

    try:
        r = requests.get(call, headers = headers, verify=False, timeout=5)
        r.raise_for_status()
    except requests.exceptions.HTTPError as errh:
        '''
        Firewall may return 5xx error when rebooting.  Need to handle a 5xx response 
        '''
        logger.debug("DeployRequestException Http Error:")
        raise DeployRequestException("Http Error:")
    except requests.exceptions.ConnectionError as errc:
        logger.debug("DeployRequestException Connection Error:")
        raise DeployRequestException("Connection Error")
    except requests.exceptions.Timeout as errt:
        logger.debug("DeployRequestException Timeout Error:")
        raise DeployRequestException("Timeout Error")
    except requests.exceptions.RequestException as err:
        logger.debug("DeployRequestException RequestException Error:")
        raise DeployRequestException("Request Error")
    else:
        return r


class DeployRequestException(Exception):
    pass

def listRecursive (d, key):
    for k, v in d.items ():
        if isinstance (v, OrderedDict):
            for found in listRecursive (v, key):
                yield found
        if k == key:
            yield v

def update_fw(fwMgtIP, api_key):
    # # Download latest applications and threats

    type = "op"
    cmd = "<request><content><upgrade><download><latest></latest></download></upgrade></content></request>"
    call = "https://%s/api/?type=%s&cmd=%s&key=%s" % (fwMgtIP, type, cmd, api_key)
    getupdate =0
    jobid = ''
    key ='job'
    while getupdate == 0:
        try:
            r = send_request(call)
            logger.info('Got response {} to request for content upgrade '.format(r.text))
        except:
            DeployRequestException
            logger.info("Didn't get http 200 response.  Try again")
        else:
            try:
                dict = xmltodict.parse(r.text)
                if isinstance(dict, OrderedDict):
                    for found in listRecursive(dict, 'job'):
                        jobid = found
            except Exception as err:
                logger.info("Got exception {} trying to parse jobid from Dict".format(err))
            if not jobid:
                logger.info('Got http 200 response but didnt get jobid')
                time.sleep(30)
            else:
                getupdate = 1


    completed = 0
    while (completed == 0):
        time.sleep(30)
        call = "https://%s/api/?type=op&cmd=<show><jobs><id>%s</id></jobs></show>&key=%s" % (fwMgtIP, jobid, api_key)
        try:
            r = send_request(call)
            logger.info('Got Response {} to show jobs '.format(r.text))
        except:
            DeployRequestException
            logger.debug("failed to get jobid this time.  Try again")
        else:
            tree = ET.fromstring(r.text)
            if tree.attrib['status'] == 'success':
                try:
                    if (tree[0][0][5].text == 'FIN'):
                        logger.debug("APP+TP download Complete " )
                        completed = 1
                    print("Download latest Applications and Threats update")
                    status = "APP+TP download Status - " + str(tree[0][0][5].text) + " " + str(
                        tree[0][0][12].text) + "% complete"
                    print('{0}\r'.format(status))
                except:
                    logger.info('Could not parse output from show jobs, with jobid {}'.format(jobid))
            else:
                logger.info('Unable to determine job status')


    # install latest anti-virus update without committing
    getjobid =0
    jobid = ''
    key ='job'
    while getjobid == 0:
        try:

            type = "op"
            cmd = "<request><anti-virus><upgrade><install><version>latest</version><commit>no</commit></install></upgrade></anti-virus></request>"
            call = "https://%s/api/?type=%s&cmd=%s&key=%s" % (fwMgtIP, type, cmd, api_key)
            r = send_request(call)
            logger.info('Got response to request AV install {}'.format(r.text))
        except:
            DeployRequestException
            logger.info("Didn't get http 200 response.  Try again")
        else:
            try:
                dict = xmltodict.parse(r.text)
                if isinstance(dict, OrderedDict):
                    for found in listRecursive(dict, 'job'):
                        jobid = found
            except Exception as err:
                logger.info("Got exception {} trying to parse jobid from Dict".format(err))
            if not jobid:
                logger.info('Got http 200 response but didnt get jobid')
                time.sleep(30)
            else:
                getjobid = 1

        completed = 0
        while (completed == 0):
            time.sleep(30)
            call = "https://%s/api/?type=op&cmd=<show><jobs><id>%s</id></jobs></show>&key=%s" % (
                fwMgtIP, jobid, api_key)
            r = send_request(call)
            tree = ET.fromstring(r.text)

            logger.debug('Got response for show job {}'.format(r.text))
            if tree.attrib['status'] == 'success':
                try:
                    if (tree[0][0][5].text == 'FIN'):
                        logger.debug("AV install Status Complete ")
                        completed = 1
                    else:
                        status = "Status - " + str(tree[0][0][5].text) + " " + str(tree[0][0][12].text) + "% complete"
                        print('{0}\r'.format(status))
                except:
                    logger.info('Could not parse output from show jobs, with jobid {}'.format(jobid))

            else:
                logger.info('Unable to determine job status')


def getApiKey(hostname, username, password):
    '''
    Generate the API key from username / password
    '''

    call = "https://%s/api/?type=keygen&user=%s&password=%s" % (hostname, username, password)

    api_key = ""
    while True:
        try:
            # response = urllib.request.urlopen(url, data=encoded_data, context=ctx).read()
            response = send_request(call)


        except DeployRequestException as updateerr:
            logger.info("No response from FW. Wait 20 secs before retry")
            time.sleep(10)
            continue

        else:
            api_key = ET.XML(response.content)[0][0].text
            logger.info("FW Management plane is Responding so checking if Dataplane is ready")
            logger.debug("Response to get_api is {}".format(response))
            return api_key



def getFirewallStatus(fwIP, api_key):
    fwip = fwIP

    """
    Gets the firewall status by sending the API request show chassis status.
    :param fwMgtIP:  IP Address of firewall interface to be probed
    :param api_key:  Panos API key
    """
    global gcontext

    url = "https://%s/api/?type=op&cmd=<show><chassis-ready></chassis-ready></show>&key=%s" % (fwip, api_key)
    # Send command to fw and see if it times out or we get a response
    logger.info("Sending command 'show chassis status' to firewall")
    try:
        response = requests.get(url, verify=False, timeout=10)
        response.raise_for_status()
    except requests.exceptions.Timeout as fwdownerr:
        logger.debug("No response from FW. So maybe not up!")
        return 'no'
        # sleep and check again?
    except requests.exceptions.HTTPError as fwstartgerr:
        '''
        Firewall may return 5xx error when rebooting.  Need to handle a 5xx response
        raise_for_status() throws HTTPError for error responses 
        '''
        logger.infor("Http Error: {}: ".format(fwstartgerr))
        return 'cmd_error'
    except requests.exceptions.RequestException as err:
        logger.debug("Got RequestException response from FW. So maybe not up!")
        return 'cmd_error'
    else:
        logger.debug("Got response to 'show chassis status' {}".format(response))

        resp_header = ET.fromstring(response.content)
        logger.debug('Response header is {}'.format(resp_header))

        if resp_header.tag != 'response':
            logger.debug("Did not get a valid 'response' string...maybe a timeout")
            return 'cmd_error'

        if resp_header.attrib['status'] == 'error':
            logger.debug("Got an error for the command")
            return 'cmd_error'

        if resp_header.attrib['status'] == 'success':
            # The fw responded with a successful command execution. So is it ready?
            for element in resp_header:
                if element.text.rstrip() == 'yes':
                    logger.info("FW Chassis is ready to accept configuration and connections")
                    return 'yes'
                else:
                    logger.info("FW Chassis not ready, still waiting for dataplane")
                    time.sleep(10)
                    return 'almost'


def update_status(key, value):
    global status_output

    if type(status_output) is not dict:
        logger.info('Creating new status_output object')
        status_output = dict()

    if key is not None and value is not None:
        status_output[key] = value

    # write status to file to future tracking
    write_status_file(status_output)


def write_status_file(message_dict):
    """
    Writes the deployment state to a dict and outputs to file for status tracking
    """
    try:
        message_json = json.dumps(message_dict)
        with open('deployment_status.json', 'w+') as dpj:
            dpj.write(message_json)

    except ValueError as ve:
        logger.error('Could not write status file!')
        print('Could not write status file!')
        sys.exit(1)


def create_azure_fileshare(share_prefix, account_name, account_key):
    # generate a unique share name to avoid overlaps in shared infra

    # FIXME - Need to remove hardcoded directoty link below

    d_dir = './WebInDeploy/bootstrap'
    share_name = "{0}-{1}".format(share_prefix.lower(), str(uuid.uuid4()))
    print('using share_name of: {}'.format(share_name))

    # archive_file_path = _create_archive_directory(files, share_prefix)

    try:
        # ignore SSL warnings - bad form, but SSL Decrypt causes issues with this
        s = requests.Session()
        s.verify = False

        file_service = FileService(account_name=account_name, account_key=account_key, request_session=s)

        # print(file_service)
        if not file_service.exists(share_name):
            file_service.create_share(share_name)

        for d in ['config', 'content', 'software', 'license']:
            print('creating directory of type: {}'.format(d))
            if not file_service.exists(share_name, directory_name=d):
                file_service.create_directory(share_name, d)

            # FIXME - We only handle bootstrap files.  May need to handle other dirs

            if d == 'config':
                for filename in os.listdir(d_dir):
                    print('creating file: {0}'.format(filename))
                    file_service.create_file_from_path(share_name, d, filename, os.path.join(d_dir, filename))

    except AttributeError as ae:
        # this can be returned on bad auth information
        print(ae)
        return "Authentication or other error creating bootstrap file_share in Azure"

    except AzureException as ahe:
        print(ahe)
        return str(ahe)
    except ValueError as ve:
        print(ve)
        return str(ve)

    print('all done')
    return share_name

def getServerStatus(IP):
    """
    Gets the server status by sending an HTTP request and checking for a 200 response code
    """
    global gcontext

    call = ("http://" + IP + "/")
    logger.info('URL request is {}'.format(call))
    # Send command to fw and see if it times out or we get a response
    count = 0
    max_count = 15
    while True:
        if count < max_count:
            try:
                count = count + 1
                r = send_request(call)
            except DeployRequestException as e:
                logger.debug("Got Invalid response".format(e))
            else:
                logger.info('Jenkins Server responded with HTTP 200 code')
                return 'server_up'
        else:
            break
    return 'server_down'


def main(username, password):

    username = username
    password = password

    WebInDeploy_vars = {
        'Admin_Username': username,
        'Admin_Password': password,
    }

    WebInFWConf_vars = {
        'Admin_Username': username,
        'Admin_Password': password
    }

    # Set run_plan to TRUE is you wish to run terraform plan before apply
    run_plan = False
    kwargs = {"auto-approve": True}

    # Class Terraform uses subprocess and setting capture_output to True will capture output
    capture_output = kwargs.pop('capture_output', False)

    if capture_output is True:
        stderr = subprocess.PIPE
        stdout = subprocess.PIPE
    else:
        # if capture output is False, then everything will essentially go to stdout and stderrf
        stderr = sys.stderr
        stdout = sys.stdout
        start_time = time.asctime()
        print(f'Starting Deployment at {start_time}\n')

    # Create Bootstrap

    tf = Terraform(working_dir='./WebInBootstrap')

    tf.cmd('init')
    if run_plan:
        # print('Calling tf.plan')
        tf.plan(capture_output=False)
    return_code1, stdout, stderr = tf.apply(capture_output=capture_output, skip_plan=True,**kwargs)

    resource_group = tf.output('Resource_Group')
    bootstrap_bucket = tf.output('Bootstrap_Bucket')
    storage_account_access_key = tf.output('Storage_Account_Access_Key')
    web_in_bootstrap_output = tf.output()

    logger.debug('Got Return code for deploy WebInDeploy {}'.format(return_code1))

    update_status('web_in_deploy_stdout', stdout)
    update_status('web_in_bootstrap_output', web_in_bootstrap_output)

    if return_code1 != 0:
        logger.info("WebInBootstrap failed")
        update_status('web_in_bootstap_status', 'error')
        update_status('web_in_bootstrap_stderr', stderr)
        print(json.dumps(status_output))
        exit(1)
    else:
        update_status('web_in_bootstrap_status', 'success')

    share_prefix = 'jenkins-demo'

    share_name = create_azure_fileshare(share_prefix, bootstrap_bucket, storage_account_access_key)

    WebInDeploy_vars.update({'Storage_Account_Access_Key': storage_account_access_key})
    WebInDeploy_vars.update({'Bootstrap_Storage_Account': bootstrap_bucket})
    WebInDeploy_vars.update({'RG_Name': resource_group})
    WebInDeploy_vars.update({'Attack_RG_Name': resource_group})
    WebInDeploy_vars.update({'Storage_Account_Fileshare': share_name})

    # Build Infrastructure

    tf = Terraform(working_dir='./WebInDeploy')
    print("vars {}".format(WebInDeploy_vars))
    tf.cmd('init')
    if run_plan:
        # print('Calling tf.plan')
        tf.plan(capture_output=False, var=WebInDeploy_vars)

    return_code1, stdout, stderr = tf.apply(var=WebInDeploy_vars, capture_output=capture_output, skip_plan=True, **kwargs)

    web_in_deploy_output = tf.output()

    logger.debug('Got Return code for deploy WebInDeploy {}'.format(return_code1))

    update_status('web_in_deploy_stdout', stdout)
    update_status('web_in_deploy_output', web_in_deploy_output)
    if return_code1 != 0:
        logger.info("WebInDeploy failed")
        update_status('web_in_deploy_status', 'error')
        update_status('web_in_deploy_stderr', stderr)
        print(json.dumps(status_output))
        exit(1)
    else:
        update_status('web_in_deploy_status', 'success')

    albDns = tf.output('ALB-DNS')
    fwMgt = tf.output('MGT-IP-FW-1')
    nlbDns = tf.output('NLB-DNS')
    fwMgtIP = tf.output('MGT-IP-FW-1')


    logger.info("Got these values from output \n\n")
    logger.info("AppGateway address is {}".format(albDns))
    logger.info("Internal loadbalancer address is {}".format(nlbDns))
    logger.info("Firewall Mgt address is {}".format(fwMgt))



    #
    # Check firewall is up and running
    # #

    api_key = getApiKey(fwMgtIP, username, password)

    while True:
        err = getFirewallStatus(fwMgtIP, api_key)
        if err == 'cmd_error':
            logger.info("Command error from fw ")

        elif err == 'no':
            logger.info("FW is not up...yet")
            # print("FW is not up...yet")
            time.sleep(60)
            continue

        elif err == 'almost':
            logger.info("MGT up waiting for dataplane")
            time.sleep(20)
            continue

        elif err == 'yes':
            logger.info("FW is up")
            break

    logger.debug('Giving the FW another 10 seconds to fully come up to avoid race conditions')
    time.sleep(10)
    fw = firewall.Firewall(hostname=fwMgtIP, api_username=username, api_password=password)
    logger.info("Updating firewall with latest content pack")

    update_fw(fwMgtIP, api_key)


    #
    # Configure Firewall
    #
    WebInFWConf_vars.update({'FW_Mgmt_IP': fwMgtIP})
    tf = Terraform(working_dir='./WebInFWConf')
    tf.cmd('init')
    kwargs = {"auto-approve": True}

    logger.info("Applying addtional config to firewall")

    WebInFWConf_vars['mgt-ipaddress-fw1'] = fwMgt

    if run_plan:
        tf.plan(capture_output=capture_output, var=WebInFWConf_vars)

    # update initial vars with generated fwMgt ip

    return_code2, stdout, stderr = tf.apply(capture_output=capture_output, skip_plan=True,
                                            var=WebInFWConf_vars, **kwargs)

    web_in_fw_conf_out = tf.output()

    update_status('web_in_fw_conf_output', web_in_fw_conf_out)
    # update_status('web_in_fw_conf_stdout', stdout)

    logger.debug('Got Return code for deploy WebInFwConf {}'.format(return_code2))

    if return_code2 != 0:
        logger.error("WebInFWConf failed")
        update_status('web_in_fw_conf_status', 'error')
        update_status('web_in_fw_conf_stderr', stderr)
        print(json.dumps(status_output))
        exit(1)
    else:
        update_status('web_in_fw_conf_status', 'success')

    logger.info("Commit changes to firewall")

    fw.commit()
    logger.info("waiting for commit")
    time.sleep(60)
    logger.info("waiting for commit")

    #
    # Check Jenkins
    #

    logger.info('Checking if Jenkins Server is ready')

    # FIXME - add outputs for all 3 dirs

    res = getServerStatus(albDns)

    if res == 'server_up':
        logger.info('Jenkins Server is ready')
        logger.info('\n\n   ### Deployment Complete ###')
        logger.info('\n\n   Connect to Jenkins Server at http://{}'.format(albDns))
    else:
        logger.info('Jenkins Server is down')
        logger.info('\n\n   ### Deployment Complete ###')

    # dump out status to stdout
    print(json.dumps(status_output))


if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='Get Terraform Params')
    parser.add_argument('-u', '--username', help='Firewall Username', required=True)
    parser.add_argument('-p', '--password', help='Firewall Password', required=True)

    args = parser.parse_args()
    username = args.username
    password = args.password

    main(username, password)





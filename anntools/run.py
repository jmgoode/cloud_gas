# Copyright (C) 2011-2018 Vas Vasiliadis
# University of Chicago
##
__author__ = 'Vas Vasiliadis <vas@uchicago.edu>'

import sys
import time
import driver
import boto3, botocore, os, shutil, json

#set resources
s3 = boto3.resource('s3', region_name='us-east-1')
dynamodb = boto3.resource('dynamodb', region_name='us-east-1')
ann_table = dynamodb.Table('jgoode_annotations')
sns = boto3.resource('sns', region_name='us-east-1')
topic = sns.Topic('arn:aws:sns:us-east-1:127134666975:jgoode_job_results')

#DIR_jobs = '/home/ubuntu/anntools/jobs/'

"""A rudimentary timer for coarse-grained profiling
"""
class Timer(object):
    def __init__(self, verbose=True):
        self.verbose = verbose

    def __enter__(self):
        self.start = time.time()
        return self

    def __exit__(self, *args):
        self.end = time.time()
        self.secs = self.end - self.start
        if self.verbose:
          print("Total runtime: {0:.6f} seconds".format(self.secs))

if __name__ == '__main__':
# Call the AnnTools pipeline
    if len(sys.argv) > 1:
        with Timer():
            driver.run(sys.argv[1], 'vcf')
        try:
            # get job id (now passed as arg from annotator.py)
            job_id = sys.argv[2]
            user = sys.argv[3]

            #set relative filepath (cwd='')
            job_folder = 'jobs/' + job_id

            items = os.listdir(job_folder) #list items in the directory of the job id folder
            log_list = [] #place to store log file when iterating directory (see below)
            annot_list = [] #place store annotation file when iterating directory contents (see below)
            for item in items: #for every item in the job id's directory
                if item.endswith('.vcf.count.log'): #if the filename ends with .vcf.count.log
                    log_list.append(item) #store it in log_list
                elif item.endswith('.annot.vcf'): #elif filename ends with .annot.vcf
                    annot_list.append(item) #store it in annot list

            #specify source of files
            log_file_path = job_folder + '/' + log_list[0] #log file path (e.g. /home/ubuntu/anntools/data/jobs/<job_id>/filename.count.log)
            ann_file_path = job_folder + '/' + annot_list[0] #annotation file path

            #specify destination on s3 bucket
            log_key = 'jgoode/' + user + '/' + job_id + '~' + log_list[0]
            ann_key = 'jgoode/' + user + '/' + job_id + '~' + annot_list[0]

            #open file
            log_data = open(log_file_path, 'rb')
            ann_data = open(ann_file_path, 'rb')

            #upload files to s3
            #source: https://zindilis.com/docs/aws/s3/upload-file-with-boto3.html
            s3.Bucket('gas-results').put_object(Key=log_key, Body=log_data)
            s3.Bucket('gas-results').put_object(Key=ann_key, Body=ann_data)

            end_time = time.time()

            #update item to completed
            ann_table.update_item(
                Key={
                    'job_id': job_id,
                },
                UpdateExpression="set s3_results_bucket = :a, s3_key_result_file = :b, s3_key_log_file = :c, complete_time = :d, job_status = :e",
                ExpressionAttributeValues={
                    ':a': 'gas-results', #results bucket
                    ':b': ann_key, #result file key
                    ':c': log_key, #log file key
                    ':d': int(time.time()), #complete time
                    ':e': 'COMPLETE' #complete status
                }
            )

            #3d) publish to job_results topic
            #source: http://boto3.readthedocs.io/en/latest/reference/services/dynamodb.html#DynamoDB.Table.get_item
            query = ann_table.get_item(Key={'job_id':job_id})
            response = query['Item']

            data = {"job_id": job_id,
                "user_id": user,
                "name": response['name'],
                "email": response['email'],
                "institution": response['institution'],
                "role": response['role'],
                "input_file_name": response['input_file_name'],
                "s3_inputs_bucket": response['s3_inputs_bucket'],
                "s3_key_input_file": response['s3_key_input_file'],
                "submit_time": int(response['submit_time']),
                's3_results_bucket':'gas-results',
                "s3_key_result_file":ann_key,
                's3_key_log_file':log_key,
                "complete_time": int(response['complete_time']),
                "job_status": 'COMPLETE'
            }

            #format message into json object
            message = json.dumps({"default":json.dumps(data,ensure_ascii=False)},ensure_ascii=False)

            #publish to results topic: http://boto3.readthedocs.io/en/latest/reference/services/sns.html#SNS.Topic.publish
            x = topic.publish(
                Message=message,
                MessageStructure='json')

            #delete job folder from local anntools instance
            shutil.rmtree(job_folder)

        except botocore.exceptions.ClientError as e:
            if e.response['Error']['Code'] == "404":
                print("The object does not exist.")
            else:
                raise
    else:
        print("A valid .vcf file must be provided as input to this program.")

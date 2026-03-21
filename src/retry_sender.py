import os
import configparser
import logging
import argparse
from pydicom import dcmread
from pynetdicom import AE, StoragePresentationContexts

# Global logger setup
logger = logging.getLogger("RetryEngine")
handler = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.propagate = False

def refresh_logger(name, level_str):
    """Updates logger identity and verbosity based on config."""
    level = getattr(logging, level_str.upper(), logging.INFO)
    logger.name = name
    logger.setLevel(level)
    for h in logger.handlers:
        h.setLevel(level)

def get_config(path):
    """Loads INI and returns ConfigParser object."""
    if not os.path.exists(path):
        return None
    conf = configparser.ConfigParser()
    conf.read(path)
    return conf

def try_send(file_path, conf, calling_aet):
    """Handles the DICOM network association and transmission."""
    # Update logger to reflect current endpoint being retried
    refresh_logger(conf.get('DEBUG', 'logname', fallback='Retry'), 
                   conf.get('DEBUG', 'loglevel', fallback='INFO'))
    
    target_host = conf.get('ENDPOINT', 'hostname')
    target_port = conf.getint('ENDPOINT', 'port')
    target_aet = conf.get('ENDPOINT', 'ae_title')

    ae = AE(ae_title=calling_aet)
    ae.requested_contexts = StoragePresentationContexts
    assoc = ae.associate(target_host, target_port, ae_title=target_aet)
    
    success = False
    if assoc.is_established:
        try:
            ds = dcmread(file_path, force=True)
            status = assoc.send_c_store(ds)
            if status and status.Status == 0x0000:
                os.remove(file_path)
                success = True
        except Exception as e:
            logger.error(f"Read/Send error for {file_path}: {e}")
        assoc.release()
    return success, f"{target_aet} ({target_host}:{target_port})"

def process_retry(working_dir):
    """Main loop to collect and retry failed DICOM transfers."""
    working_dir = os.path.abspath(working_dir)
    files_to_retry = []

    # 1. Collect all DCM files
    for root, _, files in os.walk(working_dir):
        for f in [f for f in files if f.lower().endswith('.dcm')]:
            path = os.path.join(root, f)
            files_to_retry.append({'path': path, 'mtime': os.path.getmtime(path), 'dir': root})

    if not files_to_retry:
        return False

    # 2. Sort by age (oldest first)
    files_to_retry.sort(key=lambda x: x['mtime'])

    results = {'success': set(), 'failed': set(), 'orphans': set()}

    # 3. Process transmission
    for item in files_to_retry:
        calling_aet = os.path.basename(item['dir'])
        conf_path = os.path.join(os.path.dirname(item['dir']), 'config.ini')
        conf = get_config(conf_path)

        if not conf:
            results['orphans'].add(os.path.dirname(item['dir']))
            continue

        ok, label = try_send(item['path'], conf, calling_aet)
        results['success'].add(label) if ok else results['failed'].add(label)

    return results

def print_report(results):
    """Outputs the final summary to the console."""
    print("\n" + "="*50 + "\nRETRY SUMMARY REPORT\n" + "="*50)
    for category, title in [('success', 'SUCCESSFULLY SENT TO'), 
                             ('orphans', 'ORPHAN DIRECTORIES (No config.ini)'),
                             ('failed', 'FAILED TO SEND TO')]:
        print(f"\n{title}:")
        if results[category]:
            for item in results[category]: print(f" - {item}")
        else:
            print(" None")
    print("\n" + "="*50 + "\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Recursive DICOM Retry Sender')
    parser.add_argument('--working-dir', type=str, default='/app/Working')
    args = parser.parse_args()
    
    summary = process_retry(args.working_dir)
    if summary:
        print_report(summary)
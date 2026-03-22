import os
import logging
import argparse
from pydicom import dcmread
from common import safe_dicom_send, is_within_schedule,get_config, get_conf_val

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

def try_send(file_path, conf, calling_aet):
    """
    Handles the DICOM network association and transmission.
    Returns: (status_category, label)
    """
    target_host = conf.get('ENDPOINT', 'hostname')
    target_port = conf.getint('ENDPOINT', 'port')
    target_aet = conf.get('ENDPOINT', 'ae_title')
    schedule = get_conf_val(conf, 'SCHEDULE', 'scheduled', "")
    
    logname = get_conf_val(conf, 'DEBUG', 'logname', f"Retry {target_aet}")
    refresh_logger(logname, 
                   get_conf_val(conf, 'DEBUG', 'loglevel', 'INFO'))
    

    # 1. Check Schedule
    if not is_within_schedule(schedule, logger):
        return 'skipped', f"{target_aet} (Time Window)"

    try:
        # 2. Read file
        ds = dcmread(file_path, force=True)
        
        # 3. Attempt send via common helper
        success, message = safe_dicom_send(ds, target_host, target_port, target_aet, calling_aet)
        
        label = f"{target_aet} ({target_host}:{target_port}) -> {message}"
        
        if success:
            os.remove(file_path)
            return 'success', label
        
        # 4. Differentiate between Network and DICOM logic errors
        if "Network Error" in message:
            return 'network_error', label
        else:
            return 'dicom_error', label

    except Exception as e:
        logger.error(f"Critical File Error: {e}")
        return 'dicom_error', f"File Read Error: {str(e)}"

def process_retry(working_dir):
    """Main loop to collect and retry failed DICOM transfers."""
    working_dir = os.path.abspath(working_dir)
    files_to_retry = []

    for root, _, files in os.walk(working_dir):
        for f in [f for f in files if f.lower().endswith('.dcm')]:
            path = os.path.join(root, f)
            files_to_retry.append({
                'path': path, 
                'mtime': os.path.getmtime(path), 
                'dir': root
            })

    if not files_to_retry:
        return None

    files_to_retry.sort(key=lambda x: x['mtime'])

    # Categorized results
    results = {
        'success': set(),
        'skipped': set(),
        'network_error': set(),
        'dicom_error': set(),
        'orphans': set()
    }

    for item in files_to_retry:
        calling_aet = os.path.basename(item['dir'])
        conf_path = os.path.join(os.path.dirname(item['dir']), 'config.ini')
        conf = get_config(conf_path)

        if not conf:
            results['orphans'].add(os.path.dirname(item['dir']))
            continue

        status, label = try_send(item['path'], conf, calling_aet)
        results[status].add(label)

    return results

def print_report(results):
    """Outputs a detailed summary to the console."""
    print("\n" + "="*70 + "\nRETRY SUMMARY REPORT\n" + "="*70)
    
    categories = [
        ('success', '✅ SUCCESSFULLY SENT'),
        ('skipped', '⏳ SKIPPED (OUTSIDE SCHEDULE)'),
        ('network_error', '🌐 NETWORK ERRORS (HOST DOWN/UNREACHABLE)'),
        ('dicom_error', '❌ DICOM REJECTIONS (SOP/AET REJECTED)'),
        ('orphans', '❓ ORPHAN DIRECTORIES (MISSING CONFIG)')
    ]

    for key, title in categories:
        print(f"\n{title}:")
        if results.get(key):
            for item in sorted(results[key]): 
                print(f" - {item}")
        else:
            print(" None")
            
    print("\n" + "="*70 + "\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Recursive DICOM Retry Sender')
    parser.add_argument('--working-dir', type=str, default='/app/Working')
    args = parser.parse_args()
    
    summary = process_retry(args.working_dir)
    if summary:
        print_report(summary)
    else:
        print("Retry queue is empty.")
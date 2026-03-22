import logging
import configparser
import os
from pynetdicom import AE
from datetime import datetime


def get_conf_val(conf, section, key, default=""):
    """Fetch configuration value with a fallback default."""
    try:
        return conf.get(section, key)
    except Exception:
        return default

def get_config(path):
    """Loads INI and returns ConfigParser object."""
    if not os.path.exists(path):
        return None
    conf = configparser.ConfigParser()
    conf.read(path)
    return conf

def is_within_schedule(schedule_str, logger):
    if not schedule_str:
        return True
    
    now_dt = datetime.now()
    now_time = now_dt.time()
    
    try:
        # TEST MODE ("m10 < 5")
        if schedule_str.strip().startswith("m"):
            # "m10 < 5" -> every 10. Minutes  True in the first 5 Minutes
            parts = schedule_str.replace("m", "").split("<")
            modulo = int(parts[0].strip())
            threshold = int(parts[1].strip())
            logger.debug(f"DEBUG: Current minute: {now_dt.minute}, Modulo: {modulo}, Threshold: {threshold}, Result: {(now_dt.minute % modulo) < threshold}")
            return (now_dt.minute % modulo) < threshold

        # Original Normal Time Formats 
        # 18:00-22:00, 22:30-6:00
        ranges = [r.strip() for r in schedule_str.split(',')]
        for r in ranges:
            start_str, end_str = r.split('-')
            start = datetime.strptime(start_str.strip(), "%H:%M").time()
            end = datetime.strptime(end_str.strip(), "%H:%M").time()
            
            if start <= end:
                if start <= now_time <= end: return True
            else: # Overnight range
                if now_time >= start or now_time <= end: return True
    except Exception as e:
        logger.info(f"Schedule parse error: {e}")
        return True # on error True
        
    return False
    
def safe_dicom_send(ds, host, port, target_aet, calling_aet):
    """
    Core DICOM transmission logic. 
    Returns: (bool success, str message)
    """
    ae = AE(ae_title=calling_aet)
    # Ensure the original transfer syntax is preserved
    ae.add_requested_context(ds.SOPClassUID, ds.file_meta.TransferSyntaxUID)
    
    try:
        assoc = ae.associate(host, port, ae_title=target_aet)
        if assoc.is_established:
            status = assoc.send_c_store(ds)
            assoc.release()
            
            if status and status.Status == 0x0000:
                return True, "Success"
            else:
                return False, f"DICOM Status: {hex(status.Status) if status else 'None'}"
        else:
            return False, "Association Rejected"
    except Exception as e:
        return False, f"Network Error: {str(e)}"
    
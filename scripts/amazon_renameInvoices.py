#!/usr/bin/env python3

import fitz  # PyMuPDF
import os
import re
import logging
from dotenv import load_dotenv
from datetime import datetime

load_dotenv("secrets.env")

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

# Folder containing the PDF files
folder_path = os.getcwd()

# Regular expressions for both old and new Amazon formats
order_date_pattern_new = re.compile(r"Ordered (\w+ \d{1,2}, \d{4})(?:\s+\d{1,2}:\d{2}\s*[AP]M)?")
order_date_pattern_old = re.compile(r"Order placed (\w+ \d{1,2}, \d{4})")  # New format: "Order placed"
#order_date_pattern_old = re.compile(r"Order Placed: (\w+ \d{1,2}, \d{4})")  # Old format: "Order Placed:"
order_number_pattern_content = re.compile(r"Order #[: ]+(\d+-\d+-\d+)") # Extract from PDF content (new format + new grocery format)
#order_number_pattern_content = re.compile(r"Order # (\d+-\d+-\d+)")  # Extract from PDF content (new format)
order_number_pattern_filename = re.compile(r"Amazon\.com - Order (\d+-\d+-\d+)\.pdf")  # Extract from filename (old format)

def contains_string(pdf_text, search_string):
    """Check if the PDF text contains the specified string."""
    return search_string in pdf_text

# Loop through each PDF file in the folder
for filename in os.listdir(folder_path):
    if filename.endswith(".pdf"):
        file_path = os.path.join(folder_path, filename)

        # Open the PDF
        pdf_document = fitz.open(file_path)
        try:
            text = ""
            for page_num in range(len(pdf_document)):
                text += pdf_document.load_page(page_num).get_text()

            # Check if the PDF contains the shared card
            has_shared = contains_string(text, os.environ["CARD_SHARED"])

            # Search for order date - try new format first, then old format
            order_date_match = order_date_pattern_new.search(text)
            if not order_date_match:
                order_date_match = order_date_pattern_old.search(text)
            
            if order_date_match:
                # Extract the date and convert it to YYYY-MM-DD format
                order_date_str = order_date_match.group(1)
                order_date = datetime.strptime(order_date_str, "%B %d, %Y")
                formatted_date = order_date.strftime("%Y-%m-%d")

                # Try to extract order number from PDF content first (new format)
                order_number_match = order_number_pattern_content.search(text)
                order_number = None
                
                if order_number_match:
                    order_number = order_number_match.group(1)
                    logging.info(f"Found order number in PDF content (new format): {order_number}")
                else:
                    # Fallback to extracting from filename (old format)
                    order_number_match = order_number_pattern_filename.search(filename)
                    if order_number_match:
                        order_number = order_number_match.group(1)
                        logging.info(f"Found order number in filename (old format): {order_number}")

                if order_number:
                    # Create the new file name
                    new_filename = f"{formatted_date}_Amazon_{order_number}.pdf"
                    if not has_shared:
                        new_filename = f"PERSONAL_{new_filename}"
                    new_file_path = os.path.join(folder_path, new_filename)

                    # Rename the file after the PDF document is closed
                    pdf_document.close()
                    os.rename(file_path, new_file_path)
                    logging.info(f"Renamed {filename} to {new_filename}")
                else:
                    logging.warning(f"Order number not found in PDF content or filename: {filename}")
                    pdf_document.close()
            else:
                logging.warning(f"Order date not found in PDF: {filename}")
                pdf_document.close()
        except Exception as e:
            logging.error(f"Error processing {filename}: {e}")
            pdf_document.close()
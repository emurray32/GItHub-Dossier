#!/usr/bin/env python3
"""Test script to verify blacklisting functionality."""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import (
    init_db, add_account_to_tier_0, delete_account,
    get_all_accounts, get_db_connection
)

def test_blacklisting():
    """Test the blacklisting workflow."""
    print("Testing blacklisting functionality...\n")

    # Initialize database
    print("1. Initializing database...")
    init_db()
    print("   ✓ Database initialized\n")

    # Add a test account
    print("2. Adding test account 'TestCorp'...")
    result = add_account_to_tier_0('TestCorp', 'testcorp')
    if 'error' in result:
        print(f"   ✗ Error: {result['error']}")
        return False
    account_id = result['account_id']
    print(f"   ✓ Account added with ID: {account_id}\n")

    # Verify account appears in get_all_accounts
    print("3. Verifying account appears in accounts list...")
    accounts = get_all_accounts()
    test_account = next((a for a in accounts['accounts'] if a['company_name'] == 'TestCorp'), None)
    if test_account:
        print(f"   ✓ Account found in list\n")
    else:
        print(f"   ✗ Account not found in list")
        return False

    # Blacklist the account (delete)
    print("4. Blacklisting (deleting) account...")
    deleted = delete_account(account_id)
    if deleted:
        print("   ✓ Account blacklisted\n")
    else:
        print("   ✗ Failed to blacklist account")
        return False

    # Verify account is hidden from get_all_accounts
    print("5. Verifying account is hidden from accounts list...")
    accounts = get_all_accounts()
    test_account = next((a for a in accounts['accounts'] if a['company_name'] == 'TestCorp'), None)
    if test_account is None:
        print("   ✓ Account is hidden from list\n")
    else:
        print("   ✗ Account still appears in list")
        return False

    # Verify account exists in database with status='blacklisted'
    print("6. Verifying account has status='blacklisted' in database...")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT status FROM monitored_accounts WHERE id = ?', (account_id,))
    row = cursor.fetchone()
    conn.close()

    if row and row['status'] == 'blacklisted':
        print("   ✓ Account has status='blacklisted'\n")
    else:
        print(f"   ✗ Account status is: {row['status'] if row else 'NOT FOUND'}")
        return False

    # Try to re-add the blacklisted account
    print("7. Attempting to re-add blacklisted account...")
    result = add_account_to_tier_0('TestCorp', 'testcorp')
    if 'error' in result and 'blacklisted' in result.get('message', '').lower():
        print("   ✓ Re-add prevented with blacklist error\n")
    else:
        print("   ✗ Re-add should have been prevented")
        return False

    # Clean up - actually delete the test account
    print("8. Cleaning up test data...")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM monitored_accounts WHERE id = ?', (account_id,))
    conn.commit()
    conn.close()
    print("   ✓ Test data cleaned up\n")

    print("=" * 50)
    print("✓ All blacklisting tests passed!")
    print("=" * 50)
    return True

if __name__ == '__main__':
    try:
        success = test_blacklisting()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n✗ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

@@ -201,13 +202,11 @@ public byte[] toEntropy(List<String> words) throws MnemonicException.MnemonicLen
 
     /**
      * Convert entropy data to mnemonic word list.
+     * @param entropy entropy bits, length must be a multiple of 32 bits
      */
+    public List<String> toMnemonic(byte[] entropy) {
+        checkArgument(entropy.length % 4 == 0, "entropy length not multiple of 32 bits");
+        checkArgument(entropy.length > 0, "entropy is empty");
 
         // We take initial entropy of ENT bits and compute its
         // checksum by taking first ENT / 32 bits of its SHA256 hash.

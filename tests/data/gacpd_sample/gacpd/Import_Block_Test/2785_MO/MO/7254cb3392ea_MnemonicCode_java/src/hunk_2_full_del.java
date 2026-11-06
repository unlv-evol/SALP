@@ -201,13 +202,11 @@ public byte[] toEntropy(List<String> words) throws MnemonicException.MnemonicLen
 
     /**
      * Convert entropy data to mnemonic word list.
      */
-    public List<String> toMnemonic(byte[] entropy) throws MnemonicException.MnemonicLengthException {
-        if (entropy.length % 4 > 0)
-            throw new MnemonicException.MnemonicLengthException("Entropy length not multiple of 32 bits.");
-
-        if (entropy.length == 0)
-            throw new MnemonicException.MnemonicLengthException("Entropy is empty.");
 
         // We take initial entropy of ENT bits and compute its
         // checksum by taking first ENT / 32 bits of its SHA256 hash.

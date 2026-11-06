@@ -100,21 +100,15 @@ public DeterministicSeed(SecureRandom random, int bits, String passphrase) {
     /**
      * Constructs a seed from a BIP 39 mnemonic code. See {@link MnemonicCode} for more
      * details on this scheme.
-     * @param entropy entropy bits, length must be divisible by 32
      * @param passphrase A user supplied passphrase, or an empty string if there is no passphrase
      * @param creationTimeSeconds When the seed was originally created, UNIX time.
      */
     public DeterministicSeed(byte[] entropy, String passphrase, long creationTimeSeconds) {
-        checkArgument(entropy.length % 4 == 0, "entropy size in bits not divisible by 32");
         checkArgument(entropy.length * 8 >= DEFAULT_SEED_ENTROPY_BITS, "entropy size too small");
         checkNotNull(passphrase);
 
-        try {
-            this.mnemonicCode = MnemonicCode.INSTANCE.toMnemonic(entropy);
-        } catch (MnemonicException.MnemonicLengthException e) {
-            // cannot happen
-            throw new RuntimeException(e);
-        }
         this.seed = MnemonicCode.toSeed(mnemonicCode, passphrase);
         this.encryptedMnemonicCode = null;
         this.encryptedSeed = null;

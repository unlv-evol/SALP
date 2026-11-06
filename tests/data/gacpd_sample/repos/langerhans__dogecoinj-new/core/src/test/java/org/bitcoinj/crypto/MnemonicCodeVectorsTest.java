 * This is a parametrized test class to execute all {@link MnemonicCode} test vectors.
public class MnemonicCodeVectorsTest {
    private MnemonicCode mc;
    private String vectorMnemonicCode;
    public MnemonicCodeVectorsTest(String vectorEntropy, String vectorMnemonicCode, String vectorSeed, String vectorPassphrase) {
        this.vectorMnemonicCode = vectorMnemonicCode;
        mc = new MnemonicCode();
    public void testMnemonicCode() throws Exception {
        final byte[] seed = MnemonicCode.toSeed(mnemonicCode, vectorPassphrase);
        final byte[] entropy = mc.toEntropy(WHITESPACE_SPLITTER.splitToList(vectorMnemonicCode));
        assertEquals(vectorMnemonicCode, SPACE_JOINER.join(mnemonicCode));
     * This method defines and supplies the parameters (test vectors) to be used in the testing of {@link MnemonicCode}.
